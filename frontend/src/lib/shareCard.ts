/**
 * Share cards: a result drawn to a 1080x1350 PNG in the browser (no server, no
 * extra dependency), then handed to the phone's share sheet (WhatsApp etc.)
 * or downloaded on desktop.
 */

export interface ShareCardSpec {
  kicker: string;             // small label at the top, e.g. "WEEKLY MOCK"
  headline: string;           // the big line, e.g. "82 / 100"
  subline?: string;           // under the headline, e.g. "Top 12% · passed the 75% line"
  accent?: string;            // headline colour
  body?: string[];            // a few short paragraphs (wrapped)
  footer?: string;            // e.g. a duel link
}

const W = 1080;
const H = 1350;

function wrap(ctx: CanvasRenderingContext2D, text: string, maxWidth: number): string[] {
  const lines: string[] = [];
  for (const para of text.split("\n")) {
    let line = "";
    for (const word of para.split(/\s+/)) {
      const probe = line ? `${line} ${word}` : word;
      if (ctx.measureText(probe).width > maxWidth && line) {
        lines.push(line);
        line = word;
      } else {
        line = probe;
      }
    }
    lines.push(line);
  }
  return lines;
}

export async function renderShareCard(spec: ShareCardSpec): Promise<Blob> {
  const canvas = document.createElement("canvas");
  canvas.width = W;
  canvas.height = H;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Canvas is not available");

  const bg = ctx.createLinearGradient(0, 0, W, H);
  bg.addColorStop(0, "#0b1320");
  bg.addColorStop(1, "#12324a");
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, W, H);
  ctx.fillStyle = "rgba(48,197,255,0.10)";
  ctx.beginPath();
  ctx.arc(W - 120, 140, 260, 0, Math.PI * 2);
  ctx.fill();

  const pad = 90;
  let y = 150;
  ctx.fillStyle = "#30c5ff";
  ctx.font = "700 34px system-ui, -apple-system, Segoe UI, sans-serif";
  ctx.fillText("medNAMA", pad, y);
  ctx.fillStyle = "rgba(255,255,255,0.55)";
  ctx.font = "600 26px system-ui, -apple-system, Segoe UI, sans-serif";
  ctx.fillText("FCPS prep, answered from your textbooks", pad, y + 42);

  y = 360;
  ctx.fillStyle = "rgba(255,255,255,0.6)";
  ctx.font = "700 30px system-ui, -apple-system, Segoe UI, sans-serif";
  ctx.fillText(spec.kicker.toUpperCase(), pad, y);

  y += 120;
  ctx.fillStyle = spec.accent || "#ffffff";
  ctx.font = "800 104px system-ui, -apple-system, Segoe UI, sans-serif";
  for (const line of wrap(ctx, spec.headline, W - pad * 2).slice(0, 2)) {
    ctx.fillText(line, pad, y);
    y += 112;
  }
  if (spec.subline) {
    ctx.fillStyle = "#e6edf5";
    ctx.font = "600 40px system-ui, -apple-system, Segoe UI, sans-serif";
    for (const line of wrap(ctx, spec.subline, W - pad * 2).slice(0, 2)) {
      ctx.fillText(line, pad, y);
      y += 52;
    }
  }

  y += 40;
  ctx.fillStyle = "rgba(255,255,255,0.82)";
  ctx.font = "400 34px system-ui, -apple-system, Segoe UI, sans-serif";
  for (const para of spec.body || []) {
    for (const line of wrap(ctx, para, W - pad * 2)) {
      if (y > H - 190) break;
      ctx.fillText(line, pad, y);
      y += 46;
    }
    y += 22;
  }

  ctx.fillStyle = "rgba(255,255,255,0.12)";
  ctx.fillRect(pad, H - 150, W - pad * 2, 2);
  ctx.fillStyle = "rgba(255,255,255,0.7)";
  ctx.font = "600 30px system-ui, -apple-system, Segoe UI, sans-serif";
  ctx.fillText(spec.footer || "Study with medNAMA", pad, H - 95);

  return new Promise((resolve, reject) =>
    canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("Could not draw the card"))), "image/png")
  );
}

/** Share through the device's share sheet when it accepts files; otherwise download the PNG. */
export async function shareCard(spec: ShareCardSpec, filename: string, text?: string): Promise<"shared" | "downloaded"> {
  const blob = await renderShareCard(spec);
  const file = new File([blob], filename, { type: "image/png" });
  const nav = navigator as Navigator & { canShare?: (d: ShareData) => boolean };
  if (nav.share && nav.canShare?.({ files: [file] })) {
    try {
      await nav.share({ files: [file], text });
      return "shared";
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") return "shared"; // user closed the sheet
    }
  }
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
  return "downloaded";
}
