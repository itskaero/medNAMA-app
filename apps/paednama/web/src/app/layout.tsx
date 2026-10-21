import type { Metadata, Viewport } from "next";
// medNAMA's design system (component classes), then PaedNAMA's look: black, ember and glass (scoped to body.pn).
import "@/app/globals.css";
import "~/app/paeds.css";
import { Toaster } from "sonner";

export const metadata: Metadata = {
  title: "PaedNAMA: Paediatrics IMM and FCPS-II",
  description: "Prepare for CPSP Paediatrics IMM and FCPS-II from the textbooks: the curriculum, topics, TOACS and cases.",
};

export const viewport: Viewport = { width: "device-width", initialScale: 1, themeColor: "#08080a" };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" style={{ height: "100%" }}>
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
      </head>
      <body className="pn" style={{ height: "100%" }}>
        {children}
        <Toaster position="top-center" theme="dark" richColors />
      </body>
    </html>
  );
}
