export function pageNumbers(page, pageCount) {
  const numbers = Array.from({ length: pageCount }, (_, index) => index + 1)
    .filter(number => pageCount <= 5 || number === 1 || number === pageCount || Math.abs(number - page) <= 1);
  return numbers.flatMap((number, index) => index > 0 && number - numbers[index - 1] > 1 ? [null, number] : [number]);
}

export function paginateItems(items, requestedPage, pageSize) {
  const total = items.length;
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const page = Math.max(1, Math.min(requestedPage, pageCount));
  const offset = (page - 1) * pageSize;
  return { items: items.slice(offset, offset + pageSize), total, pageCount, page, start: total ? offset + 1 : 0, end: Math.min(offset + pageSize, total) };
}
