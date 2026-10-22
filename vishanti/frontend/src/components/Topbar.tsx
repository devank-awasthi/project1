"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { logout } from "@/lib/api";
import { useSession } from "@/lib/session";

export function Mark({ size = 22 }: { size?: number }) {
  // Three interlocking links: the per-ticket chain.
  return (
    <svg width={size * 1.6} height={size} viewBox="0 0 38 22" fill="none" aria-hidden="true">
      <rect x="1.5" y="5" width="15" height="12" rx="6" stroke="currentColor" strokeWidth="2.6" />
      <rect x="11.5" y="5" width="15" height="12" rx="6" stroke="#5fb3a1" strokeWidth="2.6" />
      <rect x="21.5" y="5" width="15" height="12" rx="6" stroke="currentColor" strokeWidth="2.6" />
    </svg>
  );
}

export function Topbar() {
  const { user, ready } = useSession();
  const path = usePathname();
  const router = useRouter();
  const link = (href: string, label: string) => (
    <Link href={href} aria-current={path === href || (href !== "/" && path.startsWith(href)) ? "page" : undefined}>
      {label}
    </Link>
  );
  return (
    <header className="topbar">
      <div className="wrap">
        <Link href="/" className="brand">
          <Mark /> Vishanti
        </Link>
        <nav className="nav" aria-label="Main">
          {(!user || user.role === "student") && link("/file", "File a complaint")}
          {user?.role === "student" && link("/tickets", "My complaints")}
          {!user && link("/track", "Track with a token")}
          {user?.role === "official" && link("/desk", "My desk")}
          {user?.role === "admin" && link("/admin", "Administration")}
          {link("/record", "Public record")}
        </nav>
        {ready &&
          (user ? (
            <div className="row">
              <span className="who">
                {user.designation ?? user.full_name}
                {user.role === "official" ? `, level ${user.level}` : ""}
              </span>
              <nav className="nav" aria-label="Account">
                <button
                  onClick={() => {
                    logout();
                    router.push("/");
                  }}
                >
                  Sign out
                </button>
              </nav>
            </div>
          ) : (
            <nav className="nav" aria-label="Account" style={{ flex: "none" }}>
              {link("/login", "Sign in")}
            </nav>
          ))}
      </div>
    </header>
  );
}
