import Link from "next/link";
import { Nameplate } from "@/components/Nameplate";

export default function Home() {
  return (
    <main className="min-h-screen bg-paper">
      {/* Nav */}
      <header className="sticky top-0 z-40 border-b border-hairline/70 bg-paper/80 backdrop-blur-xl">
        <div className="mx-auto flex h-14 max-w-shell items-center justify-between px-6">
          <Link href="/" className="flex items-center gap-2">
            <Mark />
            <span className="text-[15px] font-semibold tracking-tight">MIA</span>
          </Link>
          <nav className="hidden items-center gap-8 text-[13px] text-muted md:flex">
            <a href="#how" className="transition-colors hover:text-ink">
              How it works
            </a>
            <a href="#gates" className="transition-colors hover:text-ink">
              Approval gates
            </a>
            <a href="#graph" className="transition-colors hover:text-ink">
              Integration Graph
            </a>
            <Link href="/products" className="transition-colors hover:text-ink">
              Products
            </Link>
          </nav>
          <Link
            href="/workspace"
            className="rounded-full bg-ink px-4 py-1.5 text-[13px] font-medium text-white transition-opacity hover:opacity-85"
          >
            Try for free
          </Link>
        </div>
      </header>

      {/* Hero */}
      <section className="mx-auto max-w-shell px-6 pb-24 pt-20 md:pt-28">
        <div className="grid items-center gap-16 md:grid-cols-2">
          <div>
            <p className="animate-rise font-mono text-[11px] uppercase tracking-[0.18em] text-muted">
              IDTA Digital Nameplate · ESPR ready
            </p>
            <h1
              className="mt-5 animate-rise text-[44px] font-semibold leading-[1.05] tracking-tightest md:text-[60px]"
              style={{ animationDelay: "60ms" }}
            >
              Your product data
              <br />
              already exists.
              <br />
              <span className="text-muted">It just isn&rsquo;t readable.</span>
            </h1>
            <p
              className="mt-6 max-w-md animate-rise text-[17px] leading-relaxed text-muted"
              style={{ animationDelay: "120ms" }}
            >
              MIA reads the exports, spreadsheets and datasheets you already have
              and turns them into a compliant Digital Product Passport. Agents do
              the mapping. You approve every decision before anything ships.
            </p>
            <div
              className="mt-9 flex animate-rise flex-wrap items-center gap-3"
              style={{ animationDelay: "180ms" }}
            >
              <Link
                href="/workspace"
                className="rounded-full bg-signal px-6 py-3 text-[15px] font-medium text-white transition-transform hover:scale-[1.02] active:scale-[0.99]"
              >
                Try for free
              </Link>
              <a
                href="#how"
                className="rounded-full border border-hairline px-6 py-3 text-[15px] font-medium transition-colors hover:bg-mist"
              >
                See how it works
              </a>
            </div>
            <p
              className="mt-4 animate-rise text-[13px] text-muted"
              style={{ animationDelay: "220ms" }}
            >
              No account. Runs in your browser.
            </p>
          </div>

          {/* Signature element */}
          <div className="animate-rise" style={{ animationDelay: "240ms" }}>
            <Nameplate />
          </div>
        </div>
      </section>

      {/* Problem strip */}
      <section className="border-y border-hairline bg-mist">
        <div className="mx-auto grid max-w-shell gap-10 px-6 py-16 md:grid-cols-3">
          {[
            {
              stat: "One or two",
              label:
                "people handle compliance at a typical Mittelstand manufacturer. Not a team. Not a department.",
            },
            {
              stat: "Six figures",
              label:
                "is what a consultancy charges to connect one legacy system to one data standard.",
            },
            {
              stat: "Already live",
              label:
                "Digital Product Passport rules are in force for some categories and expanding to more.",
            },
          ].map((c) => (
            <div key={c.stat}>
              <p className="text-[28px] font-semibold tracking-tight">{c.stat}</p>
              <p className="mt-2 text-[15px] leading-relaxed text-muted">
                {c.label}
              </p>
            </div>
          ))}
        </div>
      </section>

      {/* How it works */}
      <section id="how" className="mx-auto max-w-shell px-6 py-24">
        <h2 className="max-w-xl text-[34px] font-semibold leading-tight tracking-tight md:text-[42px]">
          Four agents. One pass. Every step reversible.
        </h2>
        <p className="mt-4 max-w-lg text-[17px] leading-relaxed text-muted">
          Each agent does one job and hands off. The order matters, so it&rsquo;s
          numbered.
        </p>

        <div className="mt-14 grid gap-px overflow-hidden rounded-2xl border border-hairline bg-hairline md:grid-cols-2">
          {[
            {
              n: "01",
              t: "Reading",
              d: "Pulls fields out of whatever you have — an SAP table export, an Excel parts list, a scanned datasheet — and normalises them into one consistent shape.",
            },
            {
              n: "02",
              t: "Mapping",
              d: "Matches each field to its place in the IDTA Digital Nameplate submodel, and scores how sure it is. Confident matches move on. Anything doubtful stops for you.",
            },
            {
              n: "03",
              t: "Checking",
              d: "Verifies the package against the standard's required elements before it can be exported. Gaps are reported, never quietly filled.",
            },
            {
              n: "04",
              t: "Watching",
              d: "After go-live, notices when a source field is renamed or disappears. Applies the fix it already knows, escalates anything new.",
            },
          ].map((s) => (
            <div key={s.n} className="bg-paper p-8 md:p-10">
              <p className="font-mono text-[12px] text-signal">{s.n}</p>
              <h3 className="mt-3 text-[21px] font-semibold tracking-tight">
                {s.t}
              </h3>
              <p className="mt-2 text-[15px] leading-relaxed text-muted">{s.d}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Gates */}
      <section id="gates" className="border-y border-hairline bg-mist">
        <div className="mx-auto max-w-shell px-6 py-24">
          <div className="grid gap-14 md:grid-cols-2">
            <div>
              <h2 className="text-[34px] font-semibold leading-tight tracking-tight md:text-[42px]">
                Agents execute.
                <br />
                You decide.
              </h2>
              <p className="mt-5 max-w-md text-[17px] leading-relaxed text-muted">
                Nothing reaches a passport without a person approving it. Every
                mapping carries its confidence score, the field it came from, and
                the reason it was chosen — so an auditor can follow the trail
                back.
              </p>
            </div>
            <div className="space-y-3">
              {[
                { c: 0.97, s: "NAME1", t: "ManufacturerName" },
                { c: 0.94, s: "SCHUTZART", t: "DegreeOfProtection" },
                { c: 0.64, s: "BAUJAHR", t: "YearOfConstruction" },
              ].map((r) => (
                <div
                  key={r.s}
                  className="flex items-center gap-4 rounded-xl border border-hairline bg-paper p-4"
                >
                  <div className="min-w-0 flex-1">
                    <p className="truncate font-mono text-[13px]">
                      {r.s}{" "}
                      <span className="text-muted">&rarr;</span> {r.t}
                    </p>
                    <div className="mt-2 h-1 w-full overflow-hidden rounded-full bg-mist">
                      <div
                        className="h-full rounded-full"
                        style={{
                          width: `${r.c * 100}%`,
                          background: r.c >= 0.85 ? "#1B8A5A" : "#B8760B",
                        }}
                      />
                    </div>
                  </div>
                  <span
                    className={`shrink-0 rounded-full px-2.5 py-1 text-[11px] font-medium ${
                      r.c >= 0.85
                        ? "bg-ok/10 text-ok"
                        : "bg-warn/10 text-warn"
                    }`}
                  >
                    {r.c >= 0.85 ? "Cleared" : "Needs you"}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* Graph */}
      <section id="graph" className="mx-auto max-w-shell px-6 py-24">
        <div className="grid gap-14 md:grid-cols-2 md:items-center">
          <div>
            <h2 className="text-[34px] font-semibold leading-tight tracking-tight md:text-[42px]">
              It gets faster
              <br />
              with every product.
            </h2>
            <p className="mt-5 max-w-md text-[17px] leading-relaxed text-muted">
              Every correction you make is written back to the Integration Graph.
              The next product that uses the same field starts from your decision
              instead of from a guess. The second passport takes less of your
              time than the first.
            </p>
          </div>
          <div className="rounded-2xl border border-hairline p-8">
            <div className="flex items-end gap-6">
              {[
                { l: "1st", h: 100, v: "12 checks" },
                { l: "2nd", h: 62, v: "7 checks" },
                { l: "3rd", h: 34, v: "4 checks" },
              ].map((b, i) => (
                <div key={b.l} className="flex-1 text-center">
                  <div className="flex h-40 items-end">
                    <div
                      className="w-full rounded-t-md bg-signal/15"
                      style={{
                        height: `${b.h}%`,
                        background: i === 2 ? "#0B5FD0" : undefined,
                        opacity: i === 2 ? 1 : 0.15 + i * 0.2,
                      }}
                    />
                  </div>
                  <p className="mt-3 font-mono text-[12px]">{b.l}</p>
                  <p className="text-[12px] text-muted">{b.v}</p>
                </div>
              ))}
            </div>
            <p className="mt-6 border-t border-hairline pt-4 text-[13px] text-muted">
              Manual checks needed per passport, same product family.
            </p>
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="border-t border-hairline bg-ink py-24 text-white">
        <div className="mx-auto max-w-shell px-6 text-center">
          <h2 className="mx-auto max-w-2xl text-[34px] font-semibold leading-tight tracking-tight md:text-[46px]">
            Make a passport for one of your products right now.
          </h2>
          <p className="mx-auto mt-5 max-w-md text-[17px] leading-relaxed text-white/60">
            Describe the product in your own words. MIA does the rest, and stops
            wherever it needs you.
          </p>
          <Link
            href="/workspace"
            className="mt-9 inline-block rounded-full bg-white px-7 py-3.5 text-[15px] font-medium text-ink transition-transform hover:scale-[1.02] active:scale-[0.99]"
          >
            Try for free
          </Link>
        </div>
      </section>

      <footer className="mx-auto max-w-shell px-6 py-10">
        <div className="flex flex-col justify-between gap-3 text-[13px] text-muted md:flex-row">
          <p>MIA - Mittelstand Integration Agent</p>
          <p className="font-mono text-[12px]">
            Built for the LEVEL3 AI Engineering track
          </p>
        </div>
      </footer>
    </main>
  );
}

function Mark() {
  return (
    <span className="grid h-6 w-6 place-items-center rounded-md bg-ink">
      <span className="h-1.5 w-1.5 rounded-[1px] bg-white" />
    </span>
  );
}
