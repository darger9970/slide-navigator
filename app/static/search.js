let searchOffset=0, searchSequence=0, searchJobTimer=null, searchJobSnapshot=null;
const workTerminal=new Set(["completed","completed_with_errors","failed","cancelled"]);
function workProgressView(job,container) {
  container.replaceChildren(taxElement("p",`${job.status} · ${job.stage} · ${job.current_item || ""}`));
  const bar=document.createElement("progress");bar.max=job.total || 1;if(job.total !== null) bar.value=job.processed;bar.setAttribute("aria-label","Background job progress");
  const count=taxElement("p",`${job.processed} / ${job.total ?? "unknown"} processed${job.progress_percent === null ? "" : ` (${Math.round(job.progress_percent)}%)`} · ${job.completed} completed · ${job.skipped} skipped · ${job.failed} failed`);
  const elapsed=taxElement("time");elapsed.dataset.workElapsed="";
  const byKind={}; for(const item of job.items) {byKind[item.entity_type]=(byKind[item.entity_type] || 0)+1;}
  container.append(bar,count,taxElement("p",`${byKind.deck || 0} decks · ${byKind.slide || 0} slides`),elapsed);
  if(!workTerminal.has(job.status)) container.append(taxButton("Cancel safely",async()=> {await fetchJSON(`/api/work-jobs/${job.id}/cancel`,{method:"POST"});}));
  for(const item of job.items.filter(i=>i.status==="failed")) container.append(taxElement("p",`${item.entity_type} ${item.entity_id}: ${item.message}`,"error-text"));
  if(job.error) container.append(taxElement("p",job.error,"error-text"));
  return {seconds:job.elapsed_seconds,time:performance.now(),running:!workTerminal.has(job.status),element:elapsed};
}
async function watchSearchJob(id) {
  clearTimeout(searchJobTimer);
  try { const job=await fetchJSON(`/api/work-jobs/${id}`);searchJobSnapshot=workProgressView(job,document.getElementById("search-index-progress"));
    if(!workTerminal.has(job.status)) searchJobTimer=setTimeout(()=>watchSearchJob(id),800);
  } catch(e) {document.getElementById("search-index-progress").textContent=e.message;searchJobTimer=setTimeout(()=>watchSearchJob(id),3000);}
}
setInterval(()=> {if(searchJobSnapshot) {const s=searchJobSnapshot;s.element.textContent=`Elapsed ${formatImportElapsed(s.seconds+(s.running ? (performance.now()-s.time)/1000:0))}`;}},250);
async function runSearch(reset=true) {
  if(reset) searchOffset=0; const sequence=++searchSequence;
  const status=document.getElementById("search-results-status");status.textContent="Searching…";
  const params=new URLSearchParams({q:document.getElementById("search-page-input").value,mode:document.getElementById("search-mode").value,offset:searchOffset});
  for(const key of ["kind","organisation_id","year","deck_id"]) {const value=document.getElementById(`search-${key}`).value;if(value)params.set(key,value);}
  for(const input of document.querySelectorAll("#search-tag-filters input:checked"))params.append("tag_id",input.value);
  try {const result=await fetchJSON(`/api/search?${params}`);if(sequence!==searchSequence)return;
    status.textContent=`${result.total} results · ${result.pending_index_count} awaiting current embeddings. ${result.warning || ""} ${result.ranking}`;
    const container=document.getElementById("search-results");container.replaceChildren();
    for(const item of result.items) {
      const card=item.kind === "slide" ? createSlideCard(item) : createDeckCard(item);
      if(item.kind === "deck" && item.description)card.append(taxElement("p",item.description,"search-match"));
      card.append(taxElement("p",item.matching_text,"search-match"),taxElement("small",`Hybrid rank score ${item.rank_score.toFixed(4)}`));container.append(card);
    }
    if(!result.items.length)container.append(taxElement("p","No matching library items."));
    document.getElementById("search-previous").disabled=searchOffset===0;document.getElementById("search-next").disabled=searchOffset+50>=result.total;
    updateSelectionUI();updateDeckSelectionUI();
  } catch(e) {if(sequence===searchSequence)status.textContent=e.message;}
}
function populateSearchFilters() {
  const values={organisation_id:filterOptions.organisations.map(o=>[o.name,o.id]),year:filterOptions.years.map(y=>[y,y]),deck_id:filterOptions.decks.map(d=>[d.name,d.id])};
  for(const [key,items] of Object.entries(values)) {const select=document.getElementById(`search-${key}`);if(!select)continue;const value=select.value;select.replaceChildren(new Option("All", ""),...items.map(([label,id])=>new Option(label,id)));select.value=value;}
  const container=document.getElementById("search-tag-filters");if(!container)return;
  const checked=new Set([...container.querySelectorAll("input:checked")].map(i=>i.value));container.replaceChildren();
  for(const category of filterOptions.categories || []) {
    const group=taxElement("fieldset");group.append(taxElement("legend",category.label));
    for(const tag of (filterOptions.tags || []).filter(t=>t.tag_type===category.id)) {
      const label=taxElement("label",tag.name);const input=document.createElement("input");input.type="checkbox";input.value=tag.id;input.checked=checked.has(tag.id);input.addEventListener("change",()=>runSearch());label.prepend(input);group.append(label);
    }container.append(group);
  }
}
document.addEventListener("DOMContentLoaded",async()=> {
  const page=document.getElementById("page-search");page.querySelector(".notice").remove();
  const toolbar=taxElement("div","","search-refinements");
  for(const [key,label,options] of [["mode","Search mode",[["Hybrid","hybrid"],["Exact keywords","keyword"],["Semantic","semantic"]]],["kind","Result type",[["All",""],["Slides","slide"],["Decks","deck"]]],["organisation_id","Organisation",[]],["year","Year",[]],["deck_id","Source presentation",[]]]) {
    const wrap=taxElement("label",label);const select=document.createElement("select");select.id=`search-${key}`;select.append(...options.map(([name,id])=>new Option(name,id)));select.addEventListener("change",()=>runSearch());wrap.append(select);toolbar.append(wrap);
  }page.append(toolbar);
  const details=taxElement("details");details.append(taxElement("summary","Canonical tag filters (OR within a category, AND across categories)"));const tags=taxElement("div","","tag-filters");tags.id="search-tag-filters";details.append(tags);page.append(details);
  page.append(taxButton("Build / update local search index",async()=> {const job=await fetchJSON("/api/search/index",{method:"POST"});watchSearchJob(job.job_id);}));
  const progress=taxElement("div");progress.id="search-index-progress";page.append(progress);
  const status=taxElement("p");status.id="search-results-status";status.setAttribute("role","status");page.append(status);
  const results=taxElement("div","","slide-grid");results.id="search-results";page.append(results);
  for(const [id,label,step] of [["previous","Previous",-50],["next","Next",50]]) {const button=taxButton(label,()=>{searchOffset=Math.max(0,searchOffset+step);return runSearch(false);});button.id=`search-${id}`;page.append(button);}
  document.getElementById("search-page-form").addEventListener("submit",()=>runSearch());
  document.getElementById("global-search-form").addEventListener("submit",()=>runSearch());
  document.addEventListener("library-tags-updated",populateSearchFilters);populateSearchFilters();
  // Revalidate saved IDs before presenting a restored tray; deleted slides cannot return.
  await Promise.all(selectedSlides.values().map(async slide=> {try {selectedSlides.refresh(await fetchJSON(`/api/slides/${encodeURIComponent(slide.id)}`));}catch(e) {if(e.message.includes("404") || e.message.includes("not found"))selectedSlides.remove(slide.id);}}));updateSelectionUI();
  try {const history=await fetchJSON('/api/work-jobs?kind=index');if(history.items.length)watchSearchJob(history.items[0].id);} catch(e) {progress.textContent=e.message;}
});
