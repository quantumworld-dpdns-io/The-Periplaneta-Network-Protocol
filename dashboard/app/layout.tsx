import type { Metadata, Viewport } from "next";
import "./globals.css";
import { Nav } from "@/components/ui/Nav";
import { LanguageProvider } from "@/lib/i18n";

export const metadata: Metadata = {
  title: "The Periplaneta Protocol",
  description: "Insecticide resistance evolution in an interacting Blattella germanica colony.",
  icons: { icon: "/icon.svg", shortcut: "/icon.svg", apple: "/icon.svg" },
  applicationName: "The Periplaneta Protocol",
};

export const viewport: Viewport = {
  themeColor: "#0a0e15",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-full antialiased font-mono">
        <LanguageProvider>
          <div className="max-w-[1500px] mx-auto p-3 flex flex-col gap-3">
            <Nav />
            {children}
          </div>
        </LanguageProvider>
      </body>
    </html>
  );
}
