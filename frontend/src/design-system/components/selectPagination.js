import { paginateItems } from "./pagination.js";

export const SELECT_PAGE_SIZE = 6;

export function selectPage(options, query, page) {
  const needle = query.trim().toLowerCase();
  const matching = options.filter(item => `${item.label} ${item.detail ?? ""} ${item.value}`.toLowerCase().includes(needle));
  return paginateItems(matching, page, SELECT_PAGE_SIZE);
}

export function selectedPage(options, value) {
  return Math.max(1, Math.floor(options.findIndex(item => item.value === value) / SELECT_PAGE_SIZE) + 1);
}
