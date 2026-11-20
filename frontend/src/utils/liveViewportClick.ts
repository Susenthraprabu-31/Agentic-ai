/** Map a click on a object-contain <img> to coordinates inside the visible image content. */
export function mapClickToDisplayedImage(
  img: HTMLImageElement,
  clientX: number,
  clientY: number
): { x: number; y: number; client_width: number; client_height: number } | null {
  const rect = img.getBoundingClientRect();
  const nw = img.naturalWidth;
  const nh = img.naturalHeight;
  if (!nw || !nh || rect.width < 4 || rect.height < 4) {
    return null;
  }
  const scale = Math.min(rect.width / nw, rect.height / nh);
  const dw = nw * scale;
  const dh = nh * scale;
  const offsetX = (rect.width - dw) / 2;
  const offsetY = (rect.height - dh) / 2;
  const localX = clientX - rect.left - offsetX;
  const localY = clientY - rect.top - offsetY;
  if (localX < 0 || localY < 0 || localX > dw || localY > dh) {
    return null;
  }
  return {
    x: localX,
    y: localY,
    client_width: dw,
    client_height: dh,
  };
}