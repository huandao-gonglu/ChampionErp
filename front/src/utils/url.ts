export function buildFileUrl(path: string): string {
  return `/file?path=${encodeURIComponent(path)}`
}

export function isExternalUrl(value: string): boolean {
  return /^https?:\/\//i.test(value)
}
