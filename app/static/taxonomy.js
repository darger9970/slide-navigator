/* One canonical registry for library editing, review and administration. */
function tagCategories(kind) {
  return (filterOptions.categories || []).filter(c => c[`allow_${kind}`]).map(c => c.id);
}
function taxElement(tag, text, className) {
  const el = document.createElement(tag);
  if (text) el.textContent = text;
  if (className) el.className = className;
  return el;
}
function taxButton(text, action) {
  const b = taxElement("button", text, "button button-secondary"); b.type = "button";
  b.addEventListener("click", async () => { b.disabled = true; try { await action(); } catch(e) { taxMessage(e.message, true); managementMessage(e.message,true); } finally { b.disabled = false; } });
  return b;
}
function taxMessage(text, error = false) {
  const el = document.getElementById("taxonomy-status"); el.textContent = text; el.classList.toggle("error", error);
}
async function taxMutation(url, value, method = "POST") {
  const result = await fetchJSON(url, {method, headers: {"Content-Type":"application/json"}, body: JSON.stringify(value)});
  await refreshManagedLibrary(); await loadTaxonomy(); taxMessage("Saved."); return result;
}
function taxInput(label, value = "") {
  const wrapper = taxElement("label", label); const input = document.createElement("input"); input.value = value; input.maxLength = 100; wrapper.append(input); return [wrapper, input];
}
async function loadTaxonomy() {
  try {
    const query = new URLSearchParams({q:document.getElementById("taxonomy-query").value, category:document.getElementById("taxonomy-filter-category").value, unused:document.getElementById("taxonomy-unused").checked});
    const data = await fetchJSON(`/api/taxonomy?${query}`);
    const category = document.getElementById("taxonomy-category"); const previous = category.value;
    category.replaceChildren(...data.categories.map(c => new Option(c.label,c.id))); if(previous) category.value = previous;
    const filter = document.getElementById("taxonomy-filter-category"); const current = filter.value;
    filter.replaceChildren(new Option("All categories", ""), ...data.categories.map(c => new Option(c.label,c.id))); filter.value = current;
    const rows = document.getElementById("taxonomy-items"); rows.replaceChildren();
    if(!data.items.length) rows.append(taxElement("p", "No tags match these filters."));
    for (const tag of data.items) {
      const card = taxElement("article", "", "taxonomy-card"); card.dataset.taxonomyTag = tag.id;
      card.append(taxElement("h3", `${taxonomyLabels[tag.tag_type] || tag.tag_type}: ${tag.name}`), taxElement("p", `${tag.deck_count} decks · ${tag.slide_count} slides · ${tag.status}`), taxElement("p", `Aliases: ${tag.aliases.join(", ") || "None"}`));
      const [label, name] = taxInput("Canonical name", tag.name); card.append(label);
      if(tag.similar.length) card.append(taxElement("p", `Possible duplicates: ${tag.similar.join(", ")}. Review before merging.`));
      card.append(taxButton("Rename", () => taxMutation(`/api/tags/${tag.id}`, {name:name.value}, "PATCH")));
      card.append(taxButton(tag.status === "active" ? "Archive" : "Restore", () => taxMutation(`/api/tags/${tag.id}`, {status:tag.status === "active" ? "archived" : "active"}, "PATCH")));
      const [aliasLabel, alias] = taxInput("New alias"); card.append(aliasLabel, taxButton("Add alias", () => taxMutation(`/api/tags/${tag.id}/aliases`, {name:alias.value,tag_type:tag.tag_type})));
      const target = document.createElement("select"); target.setAttribute("aria-label", `Merge ${tag.name} into`);
      target.append(new Option("Merge into…", ""), ...data.items.filter(t => t.id !== tag.id && t.tag_type === tag.tag_type && t.status === "active").map(t => new Option(t.name,t.id)));
      card.append(target, taxButton("Preview merge", () => confirmTaxonomyChange(tag, "merge", target.value)), taxButton("Preview deletion", () => confirmTaxonomyChange(tag,"delete")));
      rows.append(card);
    }
  } catch(e) { taxMessage(e.message,true); }
}
async function confirmTaxonomyChange(tag, action, target_id = null) {
  if(action === "merge" && !target_id) throw new Error("Choose a destination tag first.");
  const value = {action,target_id}; const impact = await fetchJSON(`/api/tags/${tag.id}/impact`, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(value)});
  const dialog = document.getElementById("taxonomy-confirm"); dialog.replaceChildren();
  dialog.append(taxElement("h2", `${action === "merge" ? "Merge" : "Delete"} ${tag.name}?`), taxElement("p", `${impact.deck_count} decks and ${impact.slide_count} slides are affected. ${action === "delete" ? "Assignments and this taxonomy entry will be removed." : "Assignments and aliases will use the destination tag."}`));
  dialog.append(taxButton("Cancel", () => dialog.close()), taxButton("Confirm", async () => { await taxMutation(`/api/tags/${tag.id}/apply`, {...value,token:impact.token}); dialog.close(); })); dialog.showModal();
}
document.addEventListener("DOMContentLoaded", () => {
  const nav = document.querySelector('[data-page="settings"]'); const b = taxElement("button", "Tag Manager", "nav-link"); b.dataset.page = "tags"; b.addEventListener("click", () => showPage("tags")); nav.before(b);
  const page = document.getElementById("page-tags");
  page.append(taxElement("h1","Tag Manager"));
  const [queryLabel, query] = taxInput("Search names and aliases"); query.id = "taxonomy-query"; query.addEventListener("input", loadTaxonomy);
  const unusedLabel = taxElement("label","Unused only"); const unused = document.createElement("input"); unused.type="checkbox"; unused.id="taxonomy-unused"; unused.addEventListener("change",loadTaxonomy); unusedLabel.prepend(unused);
  page.append(queryLabel, unusedLabel, taxElement("p","", "management-status")); page.lastChild.id="taxonomy-status";
  const filter = document.createElement("select"); filter.id="taxonomy-filter-category"; filter.setAttribute("aria-label","Filter tag category"); filter.append(new Option("All categories", "")); filter.addEventListener("change",loadTaxonomy); page.append(filter);
  const create = taxElement("form", "", "taxonomy-create"); const [nameLabel,name] = taxInput("New tag name"); name.required=true;
  const category = document.createElement("select"); category.id="taxonomy-category"; category.setAttribute("aria-label","Tag category");
  const submit = taxElement("button","Create tag","button button-primary"); submit.type="submit"; create.append(nameLabel,category,submit);
  create.addEventListener("submit", async e => { e.preventDefault(); try { await taxMutation("/api/tags",{name:name.value,tag_type:category.value}); name.value=""; } catch(e) {taxMessage(e.message,true);} }); page.append(create);
  const extra = taxElement("details"); extra.append(taxElement("summary","Create category"));
  const [catLabel,catName] = taxInput("Category label"); const [idLabel,id] = taxInput("Stable ID (lowercase letters, digits, underscores)");
  const scopes = {}; extra.append(catLabel,idLabel);
  for(const kind of ["deck","slide"]) { const label=taxElement("label",`Allow ${kind} assignments`); const box=document.createElement("input"); box.type="checkbox"; box.checked=true; scopes[kind]=box; label.prepend(box); extra.append(label); }
  extra.append(taxButton("Create category", () => taxMutation("/api/taxonomy/categories",{id:id.value,label:catName.value,allow_deck:scopes.deck.checked,allow_slide:scopes.slide.checked}))); page.append(extra);
  const items = taxElement("div"); items.id="taxonomy-items"; page.append(items);
  const dialog = document.createElement("dialog"); dialog.id="taxonomy-confirm"; document.body.append(dialog);
  for(const kind of ["deck","slide"]) {
    const anchor=document.getElementById(`delete-${kind}s`);
    anchor.after(taxButton("Bulk tags…", async () => {
      const ids=kind === "deck" ? [...selectedDecks.keys()] : selectedSlides.values().map(s=>s.id);
      if(!ids.length) {managementMessage("Select library items first.",true); return;}
      const data=await fetchJSON("/api/taxonomy"); dialog.replaceChildren(taxElement("h2",`Tags for ${ids.length} selected ${kind}s`));
      const select=document.createElement("select"); select.setAttribute("aria-label","Bulk canonical tag"); select.append(...data.items.filter(t=>t.status === "active" && tagCategories(kind).includes(t.tag_type)).map(t=>new Option(`${taxonomyLabels[t.tag_type]}: ${t.name}`,t.id))); dialog.append(select);
      for(const operation of ["add","remove"]) dialog.append(taxButton(operation === "add" ? "Assign" : "Remove assignment", async()=> {await taxMutation("/api/taxonomy/bulk",{kind,ids,tag_ids:[select.value],operation});dialog.close();managementMessage("Tag assignments saved.");}));
      dialog.append(taxButton("Cancel",()=>dialog.close()));dialog.showModal();
    }));
  }
});
