/**
 * The bundled PDF.js viewer (public/pdfjs) runs in an iframe.
 *
 * Links inside a PDF (e.g. the references in a NIST standard) must not replace
 * this app or the iframe. By default an embedded PDF.js sends external links to
 * `_top`, i.e. it navigates the whole app away. The viewer announces itself with
 * a `webviewerloaded` event on the parent document before it initialises, which
 * is the supported hook for setting options: open external links in a new tab.
 * (The viewer already sets `rel="noopener noreferrer nofollow"` on them.)
 */

const LINK_TARGET_BLANK = 2 // PDF.js LinkTarget.BLANK

interface ViewerLoadedEvent extends CustomEvent {
  detail: { source?: { PDFViewerApplicationOptions?: { set(name: string, value: unknown): void } } }
}

document.addEventListener("webviewerloaded", (event) => {
  const options = (event as ViewerLoadedEvent).detail?.source?.PDFViewerApplicationOptions
  options?.set("externalLinkTarget", LINK_TARGET_BLANK)
})

/** URL of the PDF.js viewer showing `fileUrl`, opened at `page` when given. */
export function pdfViewerSrc(fileUrl: string, page?: number): string {
  return `/pdfjs/web/viewer.html?file=${encodeURIComponent(fileUrl)}#${page ? `page=${page}&` : ""}pagemode=none`
}
