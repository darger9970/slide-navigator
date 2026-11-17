"""Native OPC graph assembly. Sources are read-only; unsupported dependencies fail closed."""
import copy
import hashlib
import io
import json
import posixpath
import re
import uuid
import zipfile
import tempfile
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import unquote, urlsplit
from lxml import etree as ET
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from pptx import Presentation
from app.database import connect_database
from app import work_jobs

P='http://schemas.openxmlformats.org/presentationml/2006/main'
A='http://schemas.openxmlformats.org/drawingml/2006/main'
R='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PKG='http://schemas.openxmlformats.org/package/2006/relationships'
CT='http://schemas.openxmlformats.org/package/2006/content-types'
NS={'p':P,'a':A,'r':R}
ALLOWED={'slide','slideLayout','slideMaster','notesSlide','notesMaster','theme','themeOverride','image','chart','chartUserShapes','package','oleObject','audio','video','media','hyperlink','diagramData','diagramLayout','diagramQuickStyle','diagramColors','tags','customXml','customXmlProps','font','hdphoto','chartStyle','chartColorStyle'}
XML_PARSER=ET.XMLParser(resolve_entities=False,no_network=True,remove_blank_text=False)


class Selection(BaseModel):
    model_config=ConfigDict(extra='forbid')
    ids: list[str] = Field(min_length=1,max_length=1000)


class ExportRequest(Selection):
    token: str = Field(min_length=64,max_length=64)
    filename: str = Field(default='Selected slides.pptx',min_length=1,max_length=180)
    request_id: uuid.UUID = Field(default_factory=uuid.uuid4)


class AssemblyError(ValueError): pass


def xml(data):
    result=ET.fromstring(data,parser=XML_PARSER)
    if result.getroottree().docinfo.doctype: raise AssemblyError('DTD content is not supported.')
    return result


def encode(element):
    return ET.tostring(element,xml_declaration=True,encoding='UTF-8',standalone=True)


def relationships_path(part):
    directory,name=posixpath.split(part)
    return posixpath.join(directory,'_rels',name+'.rels')


def resolve(part,target):
    parsed=urlsplit(target)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise AssemblyError(f'Unsupported internal relationship target: {target}')
    path=unquote(parsed.path)
    if '\\' in path: raise AssemblyError('Invalid package path separator.')
    normalized=posixpath.normpath(path.lstrip('/') if path.startswith('/') else posixpath.join(posixpath.dirname(part),path))
    if normalized.startswith('../') or normalized in ('..','.'): raise AssemblyError('Relationship escapes its package.')
    return normalized


class Source:
    def __init__(self,path,stack,selected):
        self.path=path
        if path.suffix.casefold()!='.pptx':
            raise AssemblyError('Native export requires PPTX. Re-import a LibreOffice-converted PPTX for legacy PPT sources; conversion fidelity needs separate review.')
        self.checksum=hashlib.sha256(path.read_bytes()).hexdigest()
        self.zip=stack.enter_context(zipfile.ZipFile(path));names=self.zip.namelist()
        if len(names)!=len(set(names)): raise AssemblyError('Source package contains duplicate part names.')
        self.names=set(names)
        if any('vbaproject' in n.casefold() or n.startswith('_xmlsignatures/') for n in names):
            raise AssemblyError('Macro-bearing or digitally signed presentations cannot be assembled without changing their security semantics.')
        types=xml(self.read('[Content_Types].xml'))
        self.defaults={r.get('Extension'):r.get('ContentType') for r in types if ET.QName(r).localname=='Default'}
        self.overrides={r.get('PartName').lstrip('/'):r.get('ContentType') for r in types if ET.QName(r).localname=='Override'}
        self.root=xml(self.read('ppt/presentation.xml'))
        if self.root.tag!='{'+P+'}presentation': raise AssemblyError('Only transitional PresentationML PPTX is supported.')
        rels={r.get('Id'):r for r in self.rels('ppt/presentation.xml')}
        self.slides={r.get('id'):resolve('ppt/presentation.xml',rels[r.get('{'+R+'}id')].get('Target')) for r in self.root.findall('p:sldIdLst/p:sldId',NS)}
        self.selected={self.slides[value] for value in selected if value in self.slides}
        if len(self.selected)!=len(selected): raise AssemblyError('A selected slide is missing from the original presentation. Re-import or refresh the library.')
        self.master_parts=[];self.notes_parts=[]
        self.global_rels=rels

    def read(self,name):
        if name not in self.names: raise AssemblyError(f'Missing package dependency: {name}')
        info=self.zip.getinfo(name)
        if info.file_size>536870912: raise AssemblyError('Package dependency exceeds the supported 512 MiB part limit.')
        return self.zip.read(name)

    def rels(self,part):
        name=relationships_path(part)
        return list(xml(self.read(name))) if name in self.names else []

    def content_type(self,part):
        result=self.overrides.get(part) or self.defaults.get(part.rsplit('.',1)[-1])
        if not result: raise AssemblyError(f'Missing content type for {part}')
        return result

    def graph_signature(self,part,seen=None):
        seen=set() if seen is None else seen
        if part in seen:return b'cycle'
        seen=seen|{part}
        data=self.read(part)
        for rel in self.rels(part):
            data+=rel.get('Id','').encode()+rel.get('Type','').encode()
            data+=rel.get('Target','').encode() if rel.get('TargetMode')=='External' else self.graph_signature(resolve(part,rel.get('Target')),seen)
        return hashlib.sha256(data).digest()


class Composer:
    def __init__(self,progress=None):
        self.parts={};self.types={};self.mapping={};self.source_map={};self.master_parts=[];self.notes_parts=[];self.warnings=set();self.manifest=[]
        self.master_id=2147483648
        self.progress=progress

    def clone(self,source,part,prefix):
        key=(prefix,part)
        if key in self.mapping: return self.mapping[key]
        if 'notesMaster+xml' in source.content_type(part) and self.notes_parts:
            existing=self.notes_parts[0];other,original=self.source_map[existing]
            if source.graph_signature(part)!=other.graph_signature(original):
                raise AssemblyError('Distinct notes master designs cannot share the single presentation notes master without changing formatting.')
            self.mapping[key]=existing
            return existing
        if part.startswith('ppt/slides/') and part.endswith('.xml') and part not in source.selected:
            raise AssemblyError('A selected slide links to an unselected slide. Include the linked slide before exporting.')
        destination=posixpath.join('ppt','sources',prefix,part)
        self.mapping[key]=destination;self.source_map[destination]=(source,part)
        content=source.read(part);content_type=source.content_type(part)
        if content_type.endswith('+xml') or content_type in ('application/xml','text/xml'):
            element=xml(content)
            if element.find('.//p:timing',NS) is not None:
                raise AssemblyError('Animation timing is not yet validated for native assembly. This slide cannot be exported safely.')
            if element.find('.//p:controls',NS) is not None:
                raise AssemblyError('ActiveX controls are unsupported for native assembly.')
            if element.tag=='{'+P+'}sldMaster':
                self.master_parts.append(destination)
                for layout in element.findall('p:sldLayoutIdLst/p:sldLayoutId',NS):
                    self.master_id+=1;layout.set('id',str(self.master_id))
                content=encode(element)
            if element.tag=='{'+P+'}notesMaster': self.notes_parts.append(destination)
        self.parts[destination]=content;self.types[destination]=content_type
        element=xml(content) if content_type.endswith('+xml') or content_type in ('application/xml','text/xml') else None
        rels=source.rels(part)
        if rels:
            root=ET.Element('{'+PKG+'}Relationships',nsmap={None:PKG})
            ids=set()
            for relation in rels:
                r=copy.deepcopy(relation);kind=r.get('Type','').rsplit('/',1)[-1]
                if r.get('Id') in ids: raise AssemblyError('Duplicate relationship ID in source package.')
                ids.add(r.get('Id'))
                if kind not in ALLOWED: raise AssemblyError(f'Unsupported dependency type: {kind}. Nothing has been silently omitted.')
                if r.get('TargetMode')=='External':
                    scheme=urlsplit(r.get('Target','')).scheme.casefold()
                    if kind!='hyperlink' or scheme not in ('https','http','mailto'):
                        raise AssemblyError(f'Unsupported linked external content: {kind}. Embed the content before exporting.')
                    self.warnings.add('External hyperlinks are retained and may open outside Slide Navigator.')
                else:
                    target=self.clone(source,resolve(part,r.get('Target')),prefix)
                    r.set('Target',posixpath.relpath(target,posixpath.dirname(destination)))
                root.append(r)
            self.parts[relationships_path(destination)]=encode(root)
        ids={r.get('Id') for r in rels}
        if element is not None:
            for node in element.iter():
                for name,value in node.attrib.items():
                    if name.startswith('{'+R+'}') and value not in ids:
                        raise AssemblyError(f'XML references a missing relationship in {part}: {value}')
        return destination

    def build(self,rows,sources):
        base=sources[rows[0]['deck_id']]
        size=base.root.find('p:sldSz',NS);notes_size=base.root.find('p:notesSz',NS)
        if size is None or notes_size is None: raise AssemblyError('Source presentation has no page dimensions.')
        default=base.root.find('p:defaultTextStyle',NS)
        default_xml=ET.tostring(default,method='c14n') if default is not None else None
        root=ET.Element('{'+P+'}presentation',nsmap={'p':P,'a':A,'r':R})
        masters=ET.SubElement(root,'{'+P+'}sldMasterIdLst');notes=ET.SubElement(root,'{'+P+'}notesMasterIdLst');slides=ET.SubElement(root,'{'+P+'}sldIdLst')
        rels=ET.Element('{'+PKG+'}Relationships',nsmap={None:PKG})
        def relate(part,kind):
            rid='rId'+str(len(rels)+1);ET.SubElement(rels,'{'+PKG+'}Relationship',Id=rid,Type=R+'/'+kind,Target=posixpath.relpath(part,'ppt'));return rid
        for index,row in enumerate(rows):
            if self.progress:self.progress(row,False)
            source=sources[row['deck_id']]
            current_size=source.root.find('p:sldSz',NS);current_notes=source.root.find('p:notesSz',NS)
            if current_size is None or any(current_size.get(k)!=size.get(k) for k in ('cx','cy')):
                raise AssemblyError('Selected presentations have different slide sizes. Native assembly cannot rescale them without changing their appearance.')
            if current_notes is None or dict(current_notes.attrib)!=dict(notes_size.attrib): raise AssemblyError('Selected sources have different notes page sizes.')
            current_default=source.root.find('p:defaultTextStyle',NS)
            if (ET.tostring(current_default,method='c14n') if current_default is not None else None)!=default_xml:
                raise AssemblyError('Sources use incompatible presentation-wide default text styles. Normalise these styles before combining them.')
            part=source.slides[row['internal_slide_id']]
            destination=self.clone(source,part,str(list(sources).index(row['deck_id'])+1))
            ET.SubElement(slides,'{'+P+'}sldId',id=str(256+index),attrib={'{'+R+'}id':relate(destination,'slide')})
            self.manifest.append({'slide_id':row['id'],'deck_id':row['deck_id'],'source_part':part,'output_part':destination,'source_sha256':source.checksum})
            if self.progress:self.progress(row,True)
        for destination in self.master_parts:
            self.master_id+=1
            ET.SubElement(masters,'{'+P+'}sldMasterId',id=str(self.master_id),attrib={'{'+R+'}id':relate(destination,'slideMaster')})
        # NotesMasterIdList has maxOccurs=1. Keep one root entry; each notes slide
        # retains its own native master relationship and dependency graph.
        if self.notes_parts:
            if len({self.parts[p] for p in self.notes_parts})>1:
                raise AssemblyError('Distinct notes master designs need consolidation before native assembly; their formatting will not be silently replaced.')
            ET.SubElement(notes,'{'+P+'}notesMasterId',attrib={'{'+R+'}id':relate(self.notes_parts[0],'notesMaster')})
        else: root.remove(notes)
        if not self.master_parts: root.remove(masters)
        root.append(copy.deepcopy(size));root.append(copy.deepcopy(notes_size))
        self.copy_globals(sources,root,relate)
        if default is not None: root.append(copy.deepcopy(default))
        self.parts['ppt/presentation.xml']=encode(root);self.types['ppt/presentation.xml']='application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml'
        self.parts['ppt/_rels/presentation.xml.rels']=encode(rels)
        package_rels=ET.Element('{'+PKG+'}Relationships',nsmap={None:PKG})
        ET.SubElement(package_rels,'{'+PKG+'}Relationship',Id='rId1',Type=R+'/officeDocument',Target='ppt/presentation.xml')
        self.parts['_rels/.rels']=encode(package_rels)
        types=ET.Element('{'+CT+'}Types',nsmap={None:CT})
        ET.SubElement(types,'{'+CT+'}Default',Extension='rels',ContentType='application/vnd.openxmlformats-package.relationships+xml')
        for part,content_type in sorted(self.types.items()): ET.SubElement(types,'{'+CT+'}Override',PartName='/'+part,ContentType=content_type)
        self.parts['[Content_Types].xml']=encode(types)
        self.warnings.add('Fonts must be available on the viewing system unless embedded. Native objects remain editable; installed fonts can affect appearance.')
        return self

    def copy_globals(self,sources,root,relate):
        table_styles=ET.Element('{'+A+'}tblStyleLst',nsmap={'a':A});style_ids={};fonts=ET.Element('{'+P+'}embeddedFontLst');font_ids={}
        for index,source in enumerate(sources.values(),1):
            for rel in source.global_rels.values():
                kind=rel.get('Type','').rsplit('/',1)[-1]
                if kind=='tableStyles':
                    table_part=resolve('ppt/presentation.xml',rel.get('Target'))
                    if source.rels(table_part):raise AssemblyError('Custom table styles with external part dependencies need explicit assembly support.')
                    table=xml(source.read(table_part))
                    if not table_styles.get('def'):table_styles.set('def',table.get('def','{5C22544A-7EE6-4342-B048-85BDC9FD1C3A}'))
                    for style in table:
                        identity=style.get('styleId');value=ET.tostring(style,method='c14n')
                        if identity in style_ids and style_ids[identity]!=value: raise AssemblyError('Conflicting custom table style definitions share an ID.')
                        if identity not in style_ids: table_styles.append(copy.deepcopy(style));style_ids[identity]=value
            embedded=source.root.find('p:embeddedFontLst',NS)
            if embedded is not None:
                for font in embedded:
                    name=font.find('p:font',NS).get('typeface');entry=copy.deepcopy(font);signature=[]
                    for variant in entry:
                        rid=variant.get('{'+R+'}id')
                        if rid:
                            rel=source.global_rels[rid];part=resolve('ppt/presentation.xml',rel.get('Target'));signature.append(source.read(part))
                            destination=self.clone(source,part,str(index));variant.set('{'+R+'}id',relate(destination,'font'))
                    if name in font_ids and font_ids[name]!=signature: raise AssemblyError('Conflicting embedded fonts share a typeface name.')
                    if name not in font_ids:fonts.append(entry);font_ids[name]=signature
        if table_styles.get('def'):
            part='ppt/tableStyles.xml';self.parts[part]=encode(table_styles);self.types[part]='application/vnd.openxmlformats-officedocument.presentationml.tableStyles+xml';relate(part,'tableStyles')
        if len(fonts):root.append(fonts)

    def write(self,path):
        with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as output:
            for part,content in self.parts.items(): output.writestr(part,content)


def library_rows(settings,ids):
    if len(ids)!=len(set(ids)): raise AssemblyError('Choose each slide once.')
    with connect_database(settings.database_path) as db:
        result=[]
        for value in ids:
            row=db.execute('SELECT s.*,d.stored_path,d.original_filename FROM slides s JOIN decks d ON d.id=s.deck_id WHERE s.id=?',(value,)).fetchone()
            if row is None: raise AssemblyError('A selected slide was deleted. Refresh the selection before exporting.')
            result.append(dict(row))
    return result


def prepare(settings,ids,stack,progress=None):
    rows=library_rows(settings,ids);sources={}
    for row in rows:
        if row['deck_id'] not in sources:
            original=Path(row['stored_path']);path=original
            if original.suffix.casefold()=='.ppt':
                original_checksum=hashlib.sha256(original.read_bytes()).hexdigest()
                from app.ingestion import _run_libreoffice_worker
                temporary=Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='slide-native-convert-')))
                path=temporary/'converted.pptx';_run_libreoffice_worker(settings,'convert',original,path)
                if hashlib.sha256(original.read_bytes()).hexdigest()!=original_checksum:raise AssemblyError('The legacy source changed during conversion.')
            source=Source(path,stack,[r['internal_slide_id'] for r in rows if r['deck_id']==row['deck_id']])
            if path!=original:
                source.path=original;source.checksum=hashlib.sha256(original.read_bytes()).hexdigest()
            sources[row['deck_id']]=source
    composer=Composer(progress).build(rows,sources)
    if any(Path(row['stored_path']).suffix.casefold()=='.ppt' for row in rows):
        composer.warnings.add('Legacy PPT was converted using LibreOffice. Native export preserves the converted objects; original binary-PPT fidelity requires review.')
    token=hashlib.sha256(json.dumps(composer.manifest,sort_keys=True).encode()).hexdigest()
    return composer,token


def preflight(settings,ids):
    try:
        with ExitStack() as stack:
            composer,token=prepare(settings,ids,stack)
            return {'token':token,'slide_count':len(ids),'deck_count':len({r['deck_id'] for r in composer.manifest}), 'warnings':sorted(composer.warnings),'part_count':len(composer.parts)}
    except (AssemblyError,OSError,zipfile.BadZipFile,ET.XMLSyntaxError,KeyError) as error:
        raise HTTPException(422,str(error)) from error


def create_job(settings,request):
    filename=request.filename.strip()
    if re.search(r'[<>:"/\\|?*\x00-\x1f]',filename) or filename in ('.','..'):
        raise HTTPException(422,'Enter a filename without path separators or reserved characters.')
    if not filename.casefold().endswith('.pptx'): filename+='.pptx'
    options={'ids':request.ids,'token':request.token,'filename':filename,'request_id':str(request.request_id)}
    return work_jobs.create(settings,'export',options)


def validate_package(path,composer):
    with zipfile.ZipFile(path) as package:
        if package.testzip(): raise AssemblyError('Output ZIP integrity validation failed.')
        names=set(package.namelist())
        for part in names:
            if not part.endswith('.rels'):continue
            owner='' if part=='_rels/.rels' else posixpath.join(posixpath.dirname(posixpath.dirname(part)),posixpath.basename(part)[:-5])
            for rel in xml(package.read(part)):
                if rel.get('TargetMode')!='External' and resolve(owner,rel.get('Target')) not in names:
                    raise AssemblyError('Output contains a missing relationship target.')
        for destination,(source,part) in composer.source_map.items():
            if destination in composer.master_parts:
                before=xml(source.read(part));after=xml(package.read(destination))
                for tree in (before,after):
                    for layout in tree.findall('p:sldLayoutIdLst/p:sldLayoutId',NS):layout.set('id','0')
                if ET.tostring(before,method='c14n')!=ET.tostring(after,method='c14n'):
                    raise AssemblyError('Slide master content changed unexpectedly.')
            if destination not in composer.master_parts and package.read(destination)!=source.read(part):
                raise AssemblyError(f'Native dependency changed unexpectedly: {part}')
    presentation=Presentation(path)
    if len(presentation.slides)!=len(composer.manifest): raise AssemblyError('Output slide count does not match selection.')
    with zipfile.ZipFile(path) as package:
        root=xml(package.read('ppt/presentation.xml'))
        rels={r.get('Id'):resolve('ppt/presentation.xml',r.get('Target')) for r in xml(package.read('ppt/_rels/presentation.xml.rels')) if r.get('TargetMode')!='External'}
        actual=[rels[r.get('{'+R+'}id')] for r in root.findall('p:sldIdLst/p:sldId',NS)]
    if actual!=[r['output_part'] for r in composer.manifest]:raise AssemblyError('Output slide order does not match selection.')


def run_job(settings,job_id):
    partial=None
    try:
        job=work_jobs.get(settings,job_id)
        if job['status']=='cancel_requested':work_jobs.update(settings,job_id,status='cancelled',stage='cancelled',completed_at=work_jobs.now());return
        if job['status']!='queued':return
        if not work_jobs.claim(settings,job_id):return
        options=json.loads(job['options'])
        work_jobs.update(settings,job_id,stage='checking source dependencies',total=len(options['ids']),processed=0,completed=0,failed=0)
        completed=0
        def progress(row,done):
            nonlocal completed
            if work_jobs.cancelled(settings,job_id):raise InterruptedError('Export cancelled safely.')
            if done:
                completed+=1
                with connect_database(settings.database_path) as db:
                    db.execute('INSERT OR REPLACE INTO work_items VALUES (?,?,?,?,?)',(job_id,'slide',row['id'],'completed',None))
            work_jobs.update(settings,job_id,stage='copying native slide dependencies',current_item=f"{row['original_filename']} · slide {row['slide_number']}",processed=completed,completed=completed)
        with ExitStack() as stack:
            composer,token=prepare(settings,options['ids'],stack,progress)
            if token!=options['token']:raise AssemblyError('Selection or source files changed since review. Run export preflight again.')
            if work_jobs.cancelled(settings,job_id):raise InterruptedError('Export cancelled safely.')
            directory=settings.indexes_dir/'exports'/job_id;directory.mkdir(parents=True,exist_ok=True)
            output=directory/options['filename'];partial=directory/'output.partial'
            work_jobs.update(settings,job_id,stage='assembling native slides',current_item=options['filename'])
            composer.write(partial)
            work_jobs.update(settings,job_id,stage='validating native package and slide order')
            validate_package(partial,composer)
            if settings.rendering_enabled:
                from app.ingestion import _run_libreoffice_worker
                # LibreOffice determines the filter by extension.
                opening=directory/'opening-check.pptx';opening.write_bytes(partial.read_bytes())
                try:
                    work_jobs.update(settings,job_id,stage='opening presentation with LibreOffice')
                    rendered=_run_libreoffice_worker(settings,'render',opening,directory/'validation-previews')
                    if len(rendered['slides'])!=len(composer.manifest): raise AssemblyError('LibreOffice did not open every exported slide.')
                finally:opening.unlink(missing_ok=True)
            if work_jobs.cancelled(settings,job_id):raise InterruptedError('Export cancelled safely.')
            for source in {s for s,p in composer.source_map.values()}:
                if hashlib.sha256(source.path.read_bytes()).hexdigest()!=source.checksum:raise AssemblyError('A source file changed during export.')
            partial.replace(output);partial=None
            (directory/'manifest.json').write_text(json.dumps({'slides':composer.manifest,'warnings':sorted(composer.warnings),'validation':'ZIP relationships, byte-preserved native dependencies, slide count/order'+(', LibreOffice opening' if settings.rendering_enabled else '')},indent=2),encoding='utf-8')
            work_jobs.update(settings,job_id,status='completed',stage='ready to save',output_path=str(output),completed_at=work_jobs.now())
    except InterruptedError:
        work_jobs.update(settings,job_id,status='cancelled',stage='cancelled',completed_at=work_jobs.now())
    except Exception as error:
        work_jobs.update(settings,job_id,status='failed',stage='failed',error=str(error)[:2000],failed=1,completed_at=work_jobs.now())
    finally:
        if partial:partial.unlink(missing_ok=True)
