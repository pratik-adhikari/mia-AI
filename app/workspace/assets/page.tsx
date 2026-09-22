"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import type { ProductLibraryItem } from "@/lib/types";
import { useAuthenticatedFetch } from "@/lib/use-authenticated-fetch";

const API_URL = process.env.NEXT_PUBLIC_MIA_API_URL ?? "";

export default function AssetsPage() {
  const authenticatedFetch = useAuthenticatedFetch();
  const [items, setItems] = useState<ProductLibraryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const load = async () => {
      try {
        const response = await authenticatedFetch(`${API_URL}/api/products`);
        if (!response.ok) {
          setError(await response.text());
          return;
        }
        setItems((await response.json()) as ProductLibraryItem[]);
      } catch (value) {
        setError(value instanceof Error ? value.message : "Unable to load assets");
      } finally {
        setLoading(false);
      }
    };
    void load();
  }, [authenticatedFetch]);

  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-8 py-8">
      <div className="mx-auto max-w-shell">
        <div className="mb-8 flex items-center justify-between">
          <div>
            <h1 className="text-[22px] font-semibold tracking-tight text-ink">
              My Assets
            </h1>
            {!loading && (
              <p className="mt-1 text-[13px] text-muted">
                {items.length} product{items.length === 1 ? "" : "s"} in your account
              </p>
            )}
          </div>
          <Link
            href="/workspace"
            className="rounded-full bg-ink px-4 py-2 text-[13px] font-medium text-white"
          >
            New passport
          </Link>
        </div>

        {error && (
          <div className="mb-5 rounded-xl border border-red-200 bg-red-50 p-4 text-[13px] text-red-700">
            Could not load durable assets: {error}
          </div>
        )}

        {loading ? (
          <div className="flex min-h-[320px] items-center justify-center text-[13px] text-muted">
            Loading assets…
          </div>
        ) : items.length === 0 ? (
          <div className="flex min-h-[360px] items-center justify-center rounded-2xl border border-hairline bg-paper">
            <div className="max-w-xs text-center">
              <p className="text-[16px] font-semibold text-ink">No products yet</p>
              <p className="mt-2 text-[13px] leading-relaxed text-muted">
                Import a product website or start a chat. Durable product history and
                published DPP versions will appear here.
              </p>
              <Link
                href="/workspace"
                className="mt-5 inline-flex rounded-full bg-ink px-5 py-2.5 text-[13px] font-medium text-white"
              >
                Start now
              </Link>
            </div>
          </div>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {items.map(({ product, latestDpp, runCount }) => (
              <Link
                key={product.id}
                href={`/products/${product.id}`}
                className="overflow-hidden rounded-2xl border border-hairline bg-paper transition-shadow hover:shadow-sm"
              >
                <div className="flex h-36 items-center justify-center border-b border-hairline bg-mist">
                  {product.imageUrl ? (
                    // External manufacturer images are intentionally left to the browser here.
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={product.imageUrl}
                      alt=""
                      className="h-full w-full object-contain p-4"
                    />
                  ) : (
                    <span className="font-mono text-[11px] text-muted">DPP / AAS</span>
                  )}
                </div>
                <div className="p-4">
                  <p className="truncate text-[14px] font-semibold text-ink">
                    {product.name ?? product.canonicalUrl}
                  </p>
                  <p className="mt-1 truncate text-[12px] text-muted">
                    {product.manufacturer ?? new URL(product.canonicalUrl).hostname}
                  </p>
                  <div className="mt-4 flex items-center gap-2 text-[11px]">
                    <span
                      className={`h-1.5 w-1.5 rounded-full ${
                        latestDpp?.deployable ? "bg-ok" : "bg-warn"
                      }`}
                    />
                    <span className={latestDpp?.deployable ? "text-ok" : "text-warn"}>
                      {latestDpp
                        ? `DPP v${latestDpp.version}`
                        : "Draft / research in progress"}
                    </span>
                    <span className="ml-auto text-muted">
                      {runCount} run{runCount === 1 ? "" : "s"}
                    </span>
                  </div>
                </div>
              </Link>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
