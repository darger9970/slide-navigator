/* Selection is independent of the visible page, filter and layout. */
(function (root) {
  class SlideSelection {
    constructor(storageKey = null) {
      this.items = new Map(); this.storageKey = storageKey;
      if(storageKey) try {
        const saved = JSON.parse(localStorage.getItem(storageKey) || "[]");
        if(Array.isArray(saved)) for(const slide of saved) if(slide && typeof slide.id === "string" && typeof slide.deck_id === "string") this.items.set(slide.id,slide);
      } catch(e) { /* A corrupt browser cache never prevents opening the library. */ }
    }
    persist() { if(this.storageKey) try {localStorage.setItem(this.storageKey,JSON.stringify(this.values()));} catch(e) {console.warn("Slide selection could not be saved locally",e);} }
    get size() { return this.items.size; }
    has(id) { return this.items.has(id); }
    values() { return [...this.items.values()]; }
    toggle(slide) {
      if (!slide || typeof slide.id !== "string" || !slide.id || !slide.deck_id) {
        throw new Error("A slide and its source deck are required.");
      }
      if (this.has(slide.id)) this.items.delete(slide.id);
      else this.items.set(slide.id, { ...slide });
      this.persist();
      return this.has(slide.id);
    }
    refresh(slide) { if (this.has(slide.id)) {this.items.set(slide.id, { ...slide }); this.persist();} }
    remove(id) { const removed=this.items.delete(id); this.persist(); return removed; }
    clear() { this.items.clear(); this.persist(); }
    move(id, targetIndex) {
      const slides = this.values();
      const sourceIndex = slides.findIndex((slide) => slide.id === id);
      if (sourceIndex < 0 || !Number.isInteger(targetIndex) || targetIndex < 0 || targetIndex >= slides.length) return false;
      const [slide] = slides.splice(sourceIndex, 1);
      slides.splice(targetIndex, 0, slide);
      this.items = new Map(slides.map((item) => [item.id, item]));
      this.persist();
      return true;
    }
    // Native export can consume this ordered manifest later. No assembly occurs here.
    manifest() {
      if (!this.size) throw new Error("Select at least one slide.");
      return {
        version: 1,
        slides: this.values().map((slide) => ({
          slide_id: slide.id, deck_id: slide.deck_id,
          internal_slide_id: slide.internal_slide_id, slide_number: slide.slide_number,
        })),
      };
    }
  }
  root.SlideSelection = SlideSelection;
  if (typeof module !== "undefined") module.exports = SlideSelection;
})(globalThis);
