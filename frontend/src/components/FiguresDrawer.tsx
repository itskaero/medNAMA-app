import React, { useState } from "react";
import { ImageOff } from "lucide-react";
import { Figure } from "../types";
// Must use the shared constant: the Docker build rewrites it to the same-origin
// proxy. A local "http://localhost:8000" fallback here made every thumbnail
// request the (unexposed) backend port directly -> "image not available".
import { API } from "@/lib/constants";

function FigureThumb({ fig, token, onFigureClick }: { fig: Figure; token: string | null; onFigureClick: (f: Figure) => void }) {
  const [failed, setFailed] = useState(false);
  const source = [fig.book_title, fig.page_number ? `p.${fig.page_number}` : null].filter(Boolean).join(", ");
  return (
    <button
      className="figure-thumb"
      onClick={() => onFigureClick(fig)}
      aria-label={`View ${fig.figure_label}${fig.caption ? `: ${fig.caption}` : ""}`}
      title={fig.caption || fig.figure_label}
    >
      <div className="figure-img-wrapper">
        {failed ? (
          <span
            style={{
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              gap: "4px",
              width: "100%",
              height: "100%",
              fontSize: "0.66rem",
              color: "var(--text-muted)",
            }}
          >
            <ImageOff size={16} />
            Figure unavailable
          </span>
        ) : (
          <img
            src={`${API}/api/figures/${fig.id}?token=${token ?? ""}`}
            alt={fig.caption || fig.figure_label}
            loading="lazy"
            onError={() => setFailed(true)}
          />
        )}
      </div>
      <span
        style={{
          marginTop: "4px",
          fontSize: "0.66rem",
          lineHeight: 1.3,
          color: "var(--text-secondary)",
          textAlign: "left",
          overflow: "hidden",
          display: "-webkit-box",
          WebkitLineClamp: 2,
          WebkitBoxOrient: "vertical",
        } as React.CSSProperties}
      >
        <strong>{fig.figure_label}</strong>
        {source ? <span style={{ color: "var(--text-muted)" }}> · {source}</span> : null}
      </span>
    </button>
  );
}

export const FiguresDrawer = React.memo(({
  figures,
  token,
  onFigureClick,
}: {
  figures: Figure[];
  token: string | null;
  onFigureClick: (f: Figure) => void;
}) => {
  if (!figures.length) return null;
  return (
    <div className="message-figures-attachment">
      <div className="figures-grid">
        {figures.map((fig) => (
          <FigureThumb key={fig.id} fig={fig} token={token} onFigureClick={onFigureClick} />
        ))}
      </div>
    </div>
  );
});

FiguresDrawer.displayName = "FiguresDrawer";
