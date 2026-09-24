"use client";

import React from "react";
import { X } from "lucide-react";
import { Figure } from "@/types";
import { API } from "@/lib/constants";

interface LightboxModalProps {
  lightboxFig: Figure | null;
  setLightboxFig: (fig: Figure | null) => void;
  token: string | null;
}

export default function LightboxModal({ lightboxFig, setLightboxFig, token }: LightboxModalProps) {
  if (!lightboxFig) return null;

  return (
    <div
      className="modal-backdrop"
      onClick={() => setLightboxFig(null)}
      role="dialog"
      aria-modal
      aria-label={`Figure: ${lightboxFig.figure_label}`}
    >
      <div className="modal-panel" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <span className="modal-title">
            {lightboxFig.figure_label}
            {lightboxFig.book_title || lightboxFig.page_number ? (
              <span style={{ color: "var(--text-muted)", marginLeft: 8 }}>
                · {[lightboxFig.book_title, lightboxFig.page_number ? `p. ${lightboxFig.page_number}` : null]
                  .filter(Boolean)
                  .join(", ")}
              </span>
            ) : null}
          </span>
          <button
            className="modal-close-btn"
            onClick={() => setLightboxFig(null)}
            aria-label="Close figure"
          >
            <X size={14} />
          </button>
        </div>
        <div className="modal-img-area">
          <img
            src={`${API}/api/figures/${lightboxFig.id}?token=${token ?? ""}`}
            alt={lightboxFig.figure_label}
          />
        </div>
        {(lightboxFig.caption || lightboxFig.reason_to_include) && (
          <div className="modal-caption-area">
            {lightboxFig.caption || lightboxFig.reason_to_include}
          </div>
        )}
      </div>
    </div>
  );
}
