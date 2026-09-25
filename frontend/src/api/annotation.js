import { apiBaseUrl, fetchForm, fetchJson } from "./http.js";

const ROOT = "/api/annotation";
const PUBLICATIONS = `${ROOT}/publications`;

export function annotationTaskImageUrl(taskId) {
  return `${apiBaseUrl()}${ROOT}/tasks/${encodeURIComponent(taskId)}/image`;
}

export function annotationProjectExportUrl(projectId) {
  return `${apiBaseUrl()}${ROOT}/projects/${encodeURIComponent(projectId)}/export`;
}

export function annotationProjectCatalogUrl(projectId) {
  return `${apiBaseUrl()}${ROOT}/projects/${encodeURIComponent(projectId)}/catalog`;
}

export function listAnnotationProjects() {
  return fetchJson(`${ROOT}/projects`);
}

export function getAnnotationStatus() {
  return fetchJson(`${ROOT}/status`);
}

export function listAnnotationTasks(projectId, page, status = "") {
  return fetchJson(`${ROOT}/projects/${encodeURIComponent(projectId)}/tasks?page=${page}&status=${encodeURIComponent(status)}`);
}

export function getAnnotationQueueSummary(projectId) {
  return fetchJson(`${ROOT}/projects/${encodeURIComponent(projectId)}/queue-summary`);
}

export function createAnnotationProject(input) {
  return fetchJson(`${ROOT}/projects`, { method: "POST", body: input });
}

export function uploadAnnotationImage(projectId, file, processedCount = 0) {
  const form = new FormData();
  form.append("image", file);
  return fetchForm(`${ROOT}/projects/${encodeURIComponent(projectId)}/images`, form, {
    fallback: `已处理 ${processedCount} 张；${file.name} 上传失败。已上传的图片会保留`,
  });
}

export function queueAnnotationTasks(projectId, ids, allowRemote) {
  return fetchJson(`${ROOT}/projects/${encodeURIComponent(projectId)}/queue`, { method: "POST", body: { ids, allow_remote: allowRemote } });
}

export function queueAllAnnotationTasks(projectId, throughSeq, allowRemote) {
  return fetchJson(`${ROOT}/projects/${encodeURIComponent(projectId)}/queue-all`, { method: "POST", body: { through_seq: throughSeq, allow_remote: allowRemote } });
}

export function confirmAnnotationTask(taskId, label, actor) {
  return fetchJson(`${ROOT}/tasks/${encodeURIComponent(taskId)}/confirm`, { method: "POST", body: { label, actor } });
}

export function validateAnnotationCatalog(catalog) {
  return fetchJson(`${ROOT}/catalog/validate`, { method: "POST", body: catalog });
}

export function listPublicationTargets() {
  return fetchJson(`${PUBLICATIONS}/targets`);
}

export function listPublicationCandidates(source, scope, page, options = {}) {
  return fetchJson(`${PUBLICATIONS}/candidates?source=${encodeURIComponent(source)}&scope=${encodeURIComponent(scope)}&page=${page}`, options);
}

export function listPublicationHistory(page) {
  return fetchJson(`${PUBLICATIONS}/?page=${page}`);
}

export function previewPublication(input) {
  return fetchJson(`${PUBLICATIONS}/preview`, { method: "POST", body: input });
}

export function transitionPublication(publicationId, action) {
  return fetchJson(`${PUBLICATIONS}/${encodeURIComponent(publicationId)}/${encodeURIComponent(action)}`, { method: "POST" });
}
