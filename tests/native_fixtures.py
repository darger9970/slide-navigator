"""Rich synthetic native presentations; no customer content."""
import io
import zipfile
from lxml import etree as ET
from PIL import Image
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE
from pptx.enum.dml import MSO_THEME_COLOR
from pptx.enum.shapes import MSO_SHAPE, PROG_ID
from pptx.util import Inches, Pt


def rich_presentation(label='Source A',accent='FF5A00',layout=6):
    p=Presentation();p.core_properties.title=label
    slide=p.slides.add_slide(p.slide_layouts[layout])
    if slide.shapes.title:slide.shapes.title.text=label
    box=slide.shapes.add_textbox(Inches(.4),Inches(.25),Inches(8),Inches(.6));box.text=label+' native objects'
    box.text_frame.paragraphs[0].runs[0].font.size=Pt(26)
    box.text_frame.paragraphs[0].runs[0].hyperlink.address='https://example.com/reference'
    shape=slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE,Inches(.4),Inches(1),Inches(2),Inches(.6));shape.text='Theme accent';shape.fill.solid();shape.fill.fore_color.theme_color=MSO_THEME_COLOR.ACCENT_1
    data=CategoryChartData();data.categories=['North','South'];data.add_series('Revenue',[12,22])
    chart=slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED,Inches(.4),Inches(2),Inches(4.3),Inches(3.3),data).chart
    chart.has_title=True;chart.chart_title.text_frame.text='Editable revenue chart'
    table=slide.shapes.add_table(2,2,Inches(5.1),Inches(2),Inches(3.3),Inches(1)).table
    table.cell(0,0).text='Region';table.cell(0,1).text='Revenue';table.cell(1,0).text='North';table.cell(1,1).text='12'
    image=Image.new('RGB',(64,40),color='#'+accent);raw=io.BytesIO();image.save(raw,format='PNG')
    slide.shapes.add_picture(io.BytesIO(raw.getvalue()),Inches(5.1),Inches(3.4),width=Inches(1.3))
    workbook=chart.part.chart_workbook.xlsx_part.blob
    slide.shapes.add_ole_object(io.BytesIO(workbook),PROG_ID.XLSX,Inches(7),Inches(3.4),width=Inches(1.3),height=Inches(.9),icon_file=io.BytesIO(raw.getvalue()),icon_width=Inches(1.3),icon_height=Inches(.9))
    slide.notes_slide.notes_text_frame.text='Native speaker notes for '+label
    raw=io.BytesIO();p.save(raw)
    with zipfile.ZipFile(io.BytesIO(raw.getvalue())) as source:
        parts={name:source.read(name) for name in source.namelist()}
    ns={'a':'http://schemas.openxmlformats.org/drawingml/2006/main'}
    theme=ET.fromstring(parts['ppt/theme/theme1.xml']);theme.find('.//a:accent1/a:srgbClr',ns).set('val',accent);parts['ppt/theme/theme1.xml']=ET.tostring(theme,xml_declaration=True,encoding='UTF-8',standalone=True)
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as result:
        for name,content in parts.items():result.writestr(name,content)
    return out.getvalue()
