import type { Metadata } from "next";
import "./globals.css";
import { LiveProvider } from "@/components/Live";
import { Topbar } from "@/components/Topbar";
import { SessionProvider } from "@/lib/session";

export const metadata: Metadata = {
  title: "Vishanti",
  description: "File a campus grievance, watch its deadline, and verify its record.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <SessionProvider>
          <LiveProvider>
            <Topbar />
            <main>
              <div className="wrap">{children}</div>
            </main>
            <footer>
              <div className="wrap spread">
                <span>Vishanti grievance redressal. Team 404Found, Webster 2026.</span>
                <span>Every entry is SHA-256 chained. Verify any complaint from its page.</span>
              </div>
            </footer>
          </LiveProvider>
        </SessionProvider>
      </body>
    </html>
  );
}
