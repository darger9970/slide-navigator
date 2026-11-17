let exportJobTimer=null, exportSnapshot=null, exportHandle=null, exportPreflight=null;
function renderNativeBuilder() {
  const target=document.querySelector("#page-builder .feature-empty, #page-builder .native-builder"); target.classList.remove("feature-empty");target.classList.add("native-builder");
  target.replaceChildren(taxElement("h2","Export selected native slides"),taxElement("p",`${selectedSlides.size} slides in saved order. Review and confirm the order before generating a PowerPoint file.`));
  target.append(taxButton("Review slide order",()=>document.getElementById("create-deck").click()));
  const [label,input]=taxInput("Output filename","Selected slides.pptx");input.id="export-filename";input.maxLength=180;target.append(label);
  const check=taxButton("Check native export",async()=> {
    if(!selectedSlides.size)throw new Error("Select slides first.");
    if(!confirmedSlideOrder || JSON.stringify(confirmedSlideOrder)!==JSON.stringify(selectedSlides.manifest()))throw new Error("Review and confirm the current slide order first.");
    const ids=selectedSlides.values().map(s=>s.id);const result=await fetchJSON("/api/exports/preflight",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({ids})});
    exportPreflight={...result,ids};const review=document.getElementById("export-check");review.replaceChildren(taxElement("p",`${result.slide_count} slides from ${result.deck_count} decks; ${result.part_count} native package parts checked.`));
    for(const warning of result.warnings)review.append(taxElement("p",warning));
    review.append(taxButton("Generate PowerPoint",startNativeExport));
  });target.append(check);
  const review=taxElement("div");review.id="export-check";target.append(review);
  const progress=taxElement("div");progress.id="export-progress";progress.setAttribute("role","status");target.append(progress);
  const history=taxElement("div");history.id="export-history";target.append(history);loadExportHistory();
}
function exportError(error) {document.getElementById("export-progress").textContent=error.message;}
async function startNativeExport() {
  try {
    if(!exportPreflight || JSON.stringify(exportPreflight.ids)!==JSON.stringify(selectedSlides.values().map(s=>s.id)))throw new Error("The selection changed. Check native export again.");
    const filename=document.getElementById("export-filename").value;
    exportHandle=null;
    if(window.showSaveFilePicker) {
      try {exportHandle=await window.showSaveFilePicker({suggestedName:filename.endsWith(".pptx")?filename:filename+".pptx",types:[{description:"PowerPoint presentation",accept:{"application/vnd.openxmlformats-officedocument.presentationml.presentation":[".pptx"]}}]});}
      catch(e) {if(e.name==="AbortError")return;throw e;}
    }
    const result=await fetchJSON("/api/exports",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({ids:exportPreflight.ids,token:exportPreflight.token,filename,request_id:crypto.randomUUID()})});
    document.getElementById("export-check").replaceChildren(taxElement("p","Export started. You can navigate or refresh and reconnect from Deck Builder."));watchExport(result.job_id);
  } catch(e) {exportError(e);}
}
async function saveNativeExport(job) {
  try {
    const filename=JSON.parse(job.options).filename;
    if(!exportHandle && window.showSaveFilePicker) {
      try {exportHandle=await window.showSaveFilePicker({suggestedName:filename,types:[{description:"PowerPoint presentation",accept:{"application/vnd.openxmlformats-officedocument.presentationml.presentation":[".pptx"]}}]});}
      catch(e) {if(e.name==="AbortError")return;throw e;}
    }
    if(exportHandle) {
      const response=await fetch(`/api/exports/${job.id}/download`);if(!response.ok)throw new Error(`Download failed (${response.status}).`);
      const writer=await exportHandle.createWritable();try {await writer.write(await response.blob());await writer.close();}catch(e){await writer.abort();throw e;}exportHandle=null;
      document.getElementById("export-progress").append(taxElement("p","PowerPoint saved."));
    } else {const link=document.createElement("a");link.href=`/api/exports/${job.id}/download`;link.download=filename;document.body.append(link);link.click();link.remove();}
  } catch(e) {exportError(e);}
}
async function watchExport(id) {
  clearTimeout(exportJobTimer);
  const target=document.getElementById("export-progress");if(!target)return;
  try {const job=await fetchJSON(`/api/work-jobs/${id}`);exportSnapshot=workProgressView(job,target);
    if(!workTerminal.has(job.status))exportJobTimer=setTimeout(()=>watchExport(id),800);
    else if(job.status==="completed")target.append(taxButton("Save PowerPoint…",()=>saveNativeExport(job)));
  }catch(e){target.textContent=`${e.message} Reconnecting…`;exportJobTimer=setTimeout(()=>watchExport(id),3000);}
}
async function loadExportHistory() {
  try {const result=await fetchJSON("/api/work-jobs?kind=export");const target=document.getElementById("export-history");if(!target)return;target.replaceChildren(taxElement("h3","Recent exports"));
    for(const job of result.items)target.append(taxButton(`${JSON.parse(job.options).filename} · ${job.status}`,()=>watchExport(job.id)));
    if(result.items.length)watchExport(result.items[0].id);
  }catch(e){exportError(e);}
}
setInterval(()=> {if(exportSnapshot) {const s=exportSnapshot;s.element.textContent=`Elapsed ${formatImportElapsed(s.seconds+(s.running ? (performance.now()-s.time)/1000:0))}`;}},250);
document.addEventListener("DOMContentLoaded",()=> {
  document.addEventListener("slide-navigator:order-confirmed",()=>{document.getElementById("selected-slides-dialog").close();showPage("builder");});
  renderNativeBuilder();
});
