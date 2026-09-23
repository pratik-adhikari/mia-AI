"use client";

import { UserButton } from "@clerk/nextjs";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuthenticationEnabled } from "@/lib/app-providers";

const ITEMS = [
  { label: "New passport", href: "/workspace", exact: true },
  { label: "Assets", href: "/workspace/assets", exact: false },
  { label: "Products", href: "/products", exact: false },
];

export function WorkspaceSidebar() {
  const pathname = usePathname();
  const authenticationEnabled = useAuthenticationEnabled();
  return (
    <aside className="fixed left-0 top-0 z-30 flex h-full w-[240px] flex-col border-r border-hairline bg-paper">
      <div className="flex h-14 shrink-0 items-center border-b border-hairline px-4">
        <Link href="/workspace" className="flex items-center gap-2.5">
          <span className="grid h-6 w-6 place-items-center rounded-md bg-ink shadow-sm">
            <span className="h-1.5 w-1.5 rounded-[1px] bg-white" />
          </span>
          <span className="text-[15px] font-semibold tracking-tight text-ink">MIA</span>
        </Link>
      </div>
      <nav className="flex flex-1 flex-col gap-1 px-3 py-3">
        {ITEMS.map((item) => {
          const active = item.exact
            ? pathname === item.href
            : pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`rounded-lg px-3 py-2 text-[13px] font-medium transition-colors ${
                active ? "bg-mist text-ink" : "text-muted hover:bg-mist/60 hover:text-ink"
              }`}
            >
              {item.label}
            </Link>
          );
        })}
      </nav>
      <div className="border-t border-hairline px-4 py-3">
        <div className="flex items-center gap-3">
          {authenticationEnabled ? (
            <>
              <UserButton />
              <span className="text-[13px] text-muted">Account</span>
            </>
          ) : (
            <span className="text-[13px] text-muted">Local test account</span>
          )}
        </div>
      </div>
    </aside>
  );
}
