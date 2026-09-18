"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import type { ProductLibraryItem } from "@/lib/types";

const API_URL = process.env.NEXT_PUBLIC_MIA_API_URL ?? "";

function productTitle(item: ProductLibraryItem) {
  return item.product.name ?? item.product.manufacturerProductId ?? "Unnamed product";
}

export default function ProductsPage() {
  const [items, setItems] = useState<ProductLibraryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    const load = async () => {
      try {
        const response = await fetch(`${API_URL}/api/products`);
        if (!response.ok) throw new Error(`Backend returned ${response.status}`);
        setItems((await response.json()) as ProductLibraryItem[]);
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "Could not load products");
      } finally {
        setLoading(false);
      }
    };
    void load();
  }, []);

  return (
    <main className="min-h-screen bg-mist">
      <header className="border-b border-hairline bg-paper">
        <div className="mx-auto flex h-14 max-w-shell items-center justify-between px-6">
          <Link href="/" className="text-[15px] font-semibold tracking-tight">MIA</Link>
          <div className="flex items-center gap-3 text-[13px]">
            <Link href="/workspace" className="text-muted hover:text-ink">Workspace</Link>
            <span className="rounded-full bg-ink px-3 py-1.5 font-medium text-white">
              Product library
            </span>
          </div>
        </div>
      </header>

      <section className="mx-auto max-w-shell px-6 py-12">
        <div className="flex items-end justify-between gap-6">
          <div>
            <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-muted">
              Durable catalogue
            </p>
            <h1 className="mt-2 text-[36px] font-semibold tracking-tight text-ink">
              Products and passports
            </h1>
            <p className="mt-2 max-w-2xl text-[15px] leading-relaxed text-muted">
              Every product attempt is retained. Successful DPPs are versioned and reused on the
              next request unless you explicitly refresh the source data.
            </p>
          </div>
          <Link
            href="/workspace"
            className="shrink-0 rounded-full bg-signal px-4 py-2 text-[13px] font-medium text-white"
          >
            Create passport
          </Link>
        </div>

        {loading && <p className="mt-10 text-sm text-muted">Loading products…</p>}
        {error && (
          <div className="mt-10 rounded-xl border border-warn/20 bg-paper p-4 text-sm text-warn">
            {error}
          </div>
        )}
        {!loading && !error && items.length === 0 && (
          <div className="mt-10 rounded-2xl border border-hairline bg-paper p-8">
            <p className="text-lg font-semibold text-ink">No products yet</p>
            <p className="mt-2 text-sm text-muted">
              Create a DPP in the workspace and the durable product record will appear here.
            </p>
          </div>
        )}

        <div className="mt-10 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {items.map((item) => (
            <Link
              key={item.product.id}
              href={`/products/${encodeURIComponent(item.product.id)}`}
              className="overflow-hidden rounded-2xl border border-hairline bg-paper transition-all hover:-translate-y-0.5 hover:border-signal/30 hover:shadow-sm"
            >
              <div className="aspect-[16/10] bg-white">
                {item.product.imageUrl ? (
                  // External manufacturer images are intentionally rendered without Next image-domain coupling.
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={item.product.imageUrl}
                    alt={productTitle(item)}
                    className="h-full w-full object-contain p-6"
                  />
                ) : (
                  <div className="grid h-full place-items-center text-xs uppercase tracking-widest text-muted">
                    No image
                  </div>
                )}
              </div>
              <div className="border-t border-hairline p-5">
                <p className="text-xs text-muted">{item.product.manufacturer ?? "Unknown manufacturer"}</p>
                <h2 className="mt-1 text-lg font-semibold tracking-tight text-ink">
                  {productTitle(item)}
                </h2>
                <div className="mt-4 flex items-center justify-between text-xs text-muted">
                  <span>{item.runCount} attempt{item.runCount === 1 ? "" : "s"}</span>
                  <span className={item.latestDpp ? "text-ok" : "text-warn"}>
                    {item.latestDpp ? `DPP v${item.latestDpp.version}` : "No completed DPP"}
                  </span>
                </div>
              </div>
            </Link>
          ))}
        </div>
      </section>
    </main>
  );
}
