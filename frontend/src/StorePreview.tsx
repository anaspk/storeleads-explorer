import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState, type ReactNode } from "react";

import { getStore } from "./api";
import type { StoreDetail } from "./types";

const TABS = ["General", "Apps & Tech", "Products", "Rank", "Notes", "Lists", "Theme"] as const;
type Tab = (typeof TABS)[number];

type Note = { id: string; body: string; createdAt: string };
const NOTES_KEY = "storeleads:store-notes:v1";
const LISTS_KEY = "storeleads:manual-lists:v1";
const FAVORITES_KEY = "storeleads:favorites:v1";

function readLocal<T>(key: string, fallback: T): T {
  try {
    const value = localStorage.getItem(key);
    return value ? JSON.parse(value) as T : fallback;
  } catch {
    return fallback;
  }
}

function present(value: unknown): boolean {
  return value !== null && value !== undefined && value !== "";
}

function text(value: unknown, fallback = "—"): string {
  return present(value) ? String(value) : fallback;
}

function number(value: unknown): string {
  if (!present(value)) return "—";
  const parsed = Number(value);
  return Number.isFinite(parsed) ? new Intl.NumberFormat().format(parsed) : String(value);
}

function date(value: unknown): string {
  if (!present(value)) return "—";
  const parsed = new Date(`${String(value)}T00:00:00`);
  return Number.isNaN(parsed.getTime())
    ? String(value)
    : new Intl.DateTimeFormat(undefined, { dateStyle: "long" }).format(parsed);
}

function money(value: unknown, currency: unknown): string {
  if (!present(value)) return "—";
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return String(value);
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: present(currency) ? String(currency) : "USD",
    maximumFractionDigits: 2,
  }).format(parsed);
}

function hostnameUrl(value: unknown): string | undefined {
  if (!present(value)) return undefined;
  const raw = String(value);
  return /^https?:\/\//i.test(raw) ? raw : `https://${raw}`;
}

function FieldGrid({ items }: { items: Array<[string, ReactNode]> }) {
  const visible = items.filter(([, value]) => value !== null && value !== undefined && value !== "");
  return <div className="preview-field-grid">{visible.map(([label, value]) => <div className="preview-field" key={label}><span>{label}</span><strong>{value}</strong></div>)}</div>;
}

function Tags({ values, empty = "None detected" }: { values: string[]; empty?: string }) {
  return values.length
    ? <div className="preview-tags">{values.map((value) => <span key={value}>{value}</span>)}</div>
    : <p className="preview-empty-inline">{empty}</p>;
}

function Section({ title, children, className = "" }: { title: string; children: ReactNode; className?: string }) {
  return <section className={`preview-section ${className}`}><h3>{title}</h3>{children}</section>;
}

function GeneralTab({ store }: { store: StoreDetail }) {
  const f = store.fields;
  const c = store.collections;
  const domain = text(f.domain);
  const social = [
    ["Instagram", f.instagram, f.instagram_url, null],
    ["Facebook", f.facebook, f.facebook_url, null],
    ["Twitter", f.twitter, f.twitter_url, f.twitter_followers],
    ["Pinterest", f.pinterest, f.pinterest_url, f.pinterest_followers],
    ["YouTube", f.youtube, f.youtube_url ?? f.youtube_channel_url, f.youtube_followers],
    ["TikTok", f.tiktok, f.tiktok_url, f.tiktok_followers],
    ["LinkedIn", f.linkedin_account, f.linkedin_url, null],
    ["WhatsApp", f.whatsapp, f.whatsapp_url, null],
  ].filter(([, account, url]) => present(account) || present(url));

  return <>
    <Section title="Store overview">
      <FieldGrid items={[
        ["Platform", text(f.platform)],
        ["Store rank", number(f.rank)],
        ["Platform rank", number(f.platform_rank)],
        ["Status", text(f.status)],
        ["Created", date(f.created)],
        ["Country", text(f.country_code)],
        ["Company location", text(f.company_location)],
        ["Estimated sales", `${money(f.estimated_monthly_sales_amount, f.estimated_monthly_sales_currency ?? f.currency)}/month`],
        ["Estimated page views", `${number(f.estimated_monthly_pageviews)}/month`],
        ["Estimated visits", `${number(f.estimated_monthly_visits)}/month`],
        ["Employees", number(f.employee_count)],
        ["Estimated products sold", number(f.products_sold)],
        ["Content language", text(f.language_code)],
        ["Merchant", text(f.merchant_name)],
      ]} />
    </Section>
    <div className="preview-two-column">
      <Section title="Categories"><Tags values={c.categories ?? []} /></Section>
      <Section title="Features"><Tags values={c.features ?? []} /></Section>
    </div>
    {(present(f.description) || present(f.meta_description)) && <Section title="Description"><p className="preview-copy">{text(f.description ?? f.meta_description)}</p></Section>}
    <Section title="Contact information">
      <div className="preview-card-grid">
        <article className="preview-contact-card"><span>Email</span>{(c.emails ?? []).length ? c.emails.map((email) => <a href={`mailto:${email}`} key={email}>{email}</a>) : <em>No email found</em>}</article>
        <article className="preview-contact-card"><span>Phone</span>{(c.phones ?? []).length ? c.phones.map((phone) => <a href={`tel:${phone}`} key={phone}>{phone}</a>) : <em>No phone found</em>}</article>
      </div>
    </Section>
    <Section title="Social media">
      {social.length ? <div className="preview-card-grid">{social.map(([name, account, url, followers]) => <article className="preview-social-card" key={String(name)}><div><span>{String(name)}</span>{present(followers) && <strong>{number(followers)} followers</strong>}</div><a href={hostnameUrl(url ?? account)} target="_blank" rel="noreferrer">{text(account, domain)} ↗</a></article>)}</div> : <p className="preview-empty-inline">No social profiles detected.</p>}
    </Section>
    {(c.cluster_domains ?? []).length > 0 && <Section title="Related domains"><Tags values={c.cluster_domains} /></Section>}
  </>;
}

function AppsTechTab({ store }: { store: StoreDetail }) {
  const f = store.fields;
  const names = store.collections.installed_apps_names ?? [];
  const urls = store.collections.installed_apps ?? [];
  const technologies = store.collections.technologies ?? [];
  return <>
    <Section title="Platform">
      <FieldGrid items={[
        ["Platform", text(f.platform)],
        ["Version", text(f.platform_version)],
        ["CMS", f.has_cms === true ? "Detected" : f.has_cms === false ? "Not detected" : "Unknown"],
        ["Headless", f.headless === true ? "Yes" : f.headless === false ? "No" : "Unknown"],
      ]} />
    </Section>
    <Section title={`Installed apps (${number(f.installed_apps_count)})`}>
      {names.length ? <div className="preview-app-grid">{names.map((name, index) => <article className="preview-app-card" key={`${name}-${index}`}><span className="preview-app-mark">A</span><div><strong>{name}</strong>{urls[index] && <a href={urls[index]} target="_blank" rel="noreferrer">Open app page ↗</a>}</div></article>)}</div> : <p className="preview-empty-inline">No installed apps were identified.</p>}
    </Section>
    <Section title={`Third-party technologies (${number(f.technologies_count)})`}><Tags values={technologies} empty="No third-party technologies detected." /></Section>
    <div className="preview-two-column">
      <Section title="Sales channels"><Tags values={store.collections.sales_channels ?? []} /></Section>
      <Section title="Shipping carriers"><Tags values={store.collections.shipping_carriers ?? []} /></Section>
    </div>
    <Section title="Change history"><p className="preview-empty-state">This dataset contains the current app and technology snapshot, but not dated installation history.</p></Section>
  </>;
}

function ProductsTab({ store }: { store: StoreDetail }) {
  const f = store.fields;
  const hasDetailedProducts = present(f.most_recent_product_title);
  return <>
    <Section title="Product summary">
      <p className="preview-copy">{hasDetailedProducts ? "Detailed product summary from the source dataset." : "This source provides aggregate product signals for this store."}</p>
      <FieldGrid items={[
        ["Products sold", number(f.products_sold)],
        ["Product variants", number(f.product_variants)],
        ["Products created · 30 days", number(f.products_created_30)],
        ["Products created · 90 days", number(f.products_created_90)],
        ["Products created · 365 days", number(f.products_created_365)],
        ["Product images", number(f.product_images)],
        ["Images added · 30 days", number(f.product_images_created_30)],
        ["Images added · 90 days", number(f.product_images_created_90)],
        ["Images added · 365 days", number(f.product_images_created_365)],
        ["Product-to-vendor ratio", number(f.product_to_vendor)],
        ["Average price", money(f.average_product_price, f.currency)],
        ["Minimum price", money(f.minimum_product_price, f.currency)],
        ["Maximum price", money(f.maximum_product_price, f.currency)],
      ]} />
    </Section>
    <Section title="Individual products"><p className="preview-empty-state">Individual product records are not included in the imported domain dataset. Add a product export or API source to enable product search here.</p></Section>
  </>;
}

function RankTab({ store }: { store: StoreDetail }) {
  const f = store.fields;
  return <>
    <Section title="Current rank">
      <div className="preview-rank-cards">
        <article><span>Store rank</span><strong>{number(f.rank)}</strong><small>{present(f.rank_percentile) ? `${number(f.rank_percentile)} percentile` : "All stores"}</small></article>
        <article><span>{text(f.platform, "Platform")} rank</span><strong>{number(f.platform_rank)}</strong><small>{present(f.platform_rank_percentile) ? `${number(f.platform_rank_percentile)} percentile` : "Within platform"}</small></article>
      </div>
    </Section>
    <Section title="Rank history"><p className="preview-empty-state">Historical ranks are not present in this snapshot. Future imports can be recorded to build this chart over time.</p></Section>
    <Section title="Common Crawl"><FieldGrid items={[["Centrality", number(f.common_crawl_centrality)], ["Page rank", number(f.common_crawl_pagerank)]]} /></Section>
  </>;
}

function NotesTab({ storeId }: { storeId: string }) {
  const [allNotes, setAllNotes] = useState<Record<string, Note[]>>(() => readLocal(NOTES_KEY, {}));
  const [draft, setDraft] = useState("");
  const notes = allNotes[storeId] ?? [];
  const addNote = () => {
    const body = draft.trim();
    if (!body) return;
    const next = { ...allNotes, [storeId]: [...notes, { id: crypto.randomUUID(), body, createdAt: new Date().toISOString() }] };
    setAllNotes(next); localStorage.setItem(NOTES_KEY, JSON.stringify(next)); setDraft("");
  };
  const removeNote = (id: string) => {
    const next = { ...allNotes, [storeId]: notes.filter((note) => note.id !== id) };
    setAllNotes(next); localStorage.setItem(NOTES_KEY, JSON.stringify(next));
  };
  return <Section title="Personal notes">
    <p className="preview-copy">Keep context and follow-up details for this store in this browser.</p>
    <div className="preview-note-compose"><textarea value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); addNote(); } }} placeholder="Write a note…" /><button className="button button--primary" disabled={!draft.trim()} onClick={addNote}>Add note</button></div>
    <small className="preview-hint">Press Enter to add · Shift + Enter for a new line</small>
    {notes.length > 0 && <div className="preview-notes">{[...notes].reverse().map((note) => <article key={note.id}><p>{note.body}</p><div><time>{new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(note.createdAt))}</time><button onClick={() => removeNote(note.id)}>Delete</button></div></article>)}</div>}
  </Section>;
}

function ListsTab({ storeId }: { storeId: string }) {
  const [memberships, setMemberships] = useState<Record<string, string[]>>(() => readLocal(LISTS_KEY, {}));
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const lists = memberships[storeId] ?? [];
  const persist = (nextLists: string[]) => {
    const next = { ...memberships, [storeId]: nextLists };
    setMemberships(next); localStorage.setItem(LISTS_KEY, JSON.stringify(next));
  };
  const add = () => {
    const value = name.trim();
    if (!value || lists.includes(value)) return;
    persist([...lists, value]); setName(""); setAdding(false);
  };
  return <Section title="Manual lists">
    {lists.length ? <div className="preview-list-memberships">{lists.map((list) => <div key={list}><span>{list}</span><button onClick={() => persist(lists.filter((item) => item !== list))}>Remove</button></div>)}</div> : <div className="preview-empty-state"><strong>This store does not belong to any lists.</strong><span>Only lists to which the store is manually added appear here.</span></div>}
    {adding ? <div className="preview-list-add"><input autoFocus value={name} onChange={(event) => setName(event.target.value)} onKeyDown={(event) => event.key === "Enter" && add()} placeholder="List name" /><button className="button button--primary" disabled={!name.trim()} onClick={add}>Add</button><button className="button button--quiet" onClick={() => setAdding(false)}>Cancel</button></div> : <button className="button button--primary preview-centered-action" onClick={() => setAdding(true)}>+ Add to list</button>}
  </Section>;
}

function ThemeTab({ store }: { store: StoreDetail }) {
  const f = store.fields;
  return <>
    <Section title="Current theme"><FieldGrid items={[["Vendor", text(f.theme_vendor)], ["Name", text(f.theme)], ["Style", text(f.theme_style)], ["Monthly spend", money(f.theme_spend, f.currency)]]} /></Section>
    <Section title="Change history">
      {present(f.last_theme) || present(f.last_theme_changed) ? <div className="preview-timeline"><article><i /><div><strong>Theme changed from {text(f.last_theme, "an unknown theme")} to {text(f.theme, "the current theme")}</strong><time>{date(f.last_theme_changed)}</time></div></article></div> : <p className="preview-empty-state">No prior theme change is included in this snapshot.</p>}
    </Section>
  </>;
}

export function StorePreview({ storeId, onClose }: { storeId: string; onClose: () => void }) {
  const [tab, setTab] = useState<Tab>("General");
  const [favorites, setFavorites] = useState<string[]>(() => readLocal(FAVORITES_KEY, []));
  const query = useQuery({ queryKey: ["store-detail", storeId], queryFn: () => getStore(storeId), staleTime: 5 * 60_000, retry: false });
  const favorite = favorites.includes(storeId);
  const title = useMemo(() => query.data ? text(query.data.fields.domain, "Store details") : "Store details", [query.data]);

  useEffect(() => {
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const closeOnEscape = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    document.addEventListener("keydown", closeOnEscape);
    return () => { document.body.style.overflow = previous; document.removeEventListener("keydown", closeOnEscape); };
  }, [onClose]);

  const toggleFavorite = () => {
    const next = favorite ? favorites.filter((id) => id !== storeId) : [...favorites, storeId];
    setFavorites(next); localStorage.setItem(FAVORITES_KEY, JSON.stringify(next));
  };

  return <div className="preview-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
    <aside className="store-preview" role="dialog" aria-modal="true" aria-labelledby="store-preview-title">
      <header className="preview-header"><div className="preview-identity"><span>{title.slice(0, 1).toUpperCase()}</span><div><h2 id="store-preview-title">{title}</h2><p>{query.data ? text(query.data.fields.title ?? query.data.fields.merchant_name, "Store") : "Loading store…"}</p></div></div><button className="preview-close" autoFocus onClick={onClose} aria-label="Close store preview">×</button></header>
      <nav className="preview-tabs" aria-label="Store details">{TABS.map((item) => <button role="tab" aria-selected={tab === item} className={tab === item ? "active" : ""} key={item} onClick={() => setTab(item)}>{item}</button>)}</nav>
      <div className="preview-content">
        {query.isPending && <div className="preview-state"><div className="loader"/><span>Loading store details…</span></div>}
        {query.isError && <div className="preview-state preview-state--error"><strong>Could not load this store</strong><span>{query.error.message}</span><button className="button button--quiet" onClick={() => void query.refetch()}>Retry</button></div>}
        {query.data && <>
          {tab === "General" && <GeneralTab store={query.data} />}
          {tab === "Apps & Tech" && <AppsTechTab store={query.data} />}
          {tab === "Products" && <ProductsTab store={query.data} />}
          {tab === "Rank" && <RankTab store={query.data} />}
          {tab === "Notes" && <NotesTab storeId={storeId} />}
          {tab === "Lists" && <ListsTab storeId={storeId} />}
          {tab === "Theme" && <ThemeTab store={query.data} />}
        </>}
      </div>
      <footer className="preview-footer"><button className={`button ${favorite ? "button--quiet" : "button--primary"}`} onClick={toggleFavorite}>{favorite ? "− Remove from favorites" : "+ Add to favorites"}</button><button className="button button--quiet" onClick={onClose}>× Close</button></footer>
    </aside>
  </div>;
}
