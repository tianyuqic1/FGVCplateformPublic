// Assemble first, then update the UI atomically: failed pages never select a
// misleading partial result. Publication still revalidates every source ID.
export async function selectAllCandidates(loadPage, limit = 1000) {
  const first = await loadPage(1);
  if (first.total > limit) throw new Error(`当前有 ${first.total} 张，超过单批 ${limit} 张上限，请使用“选择本页”分批发布。`);
  const size = first.page_size || 12;
  const items = [...first.items];
  for (let page = 2; page <= Math.ceil(first.total / size); page++) {
    const next = await loadPage(page);
    if (next.total !== first.total) throw new Error("待发布列表已变化，请重新全选。");
    items.push(...next.items);
  }
  if (items.length !== first.total || new Set(items.map(t => t.id)).size !== first.total) throw new Error("待发布列表已变化，请重新全选。");
  return items;
}
