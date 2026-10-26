import { useCallback, useEffect, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import {
  ArrowDownLeft,
  ArrowRight,
  ArrowUpRight,
  BarChart3,
  Bell,
  BookOpen,
  BriefcaseBusiness,
  Building2,
  Check,
  CheckCircle2,
  ChevronRight,
  CircleDollarSign,
  Clock3,
  Download,
  FileCheck2,
  FileText,
  LayoutDashboard,
  ListTodo,
  Loader2,
  LogOut,
  Menu,
  Package,
  Plus,
  RefreshCw,
  Search,
  Settings2,
  ShieldCheck,
  TrendingUp,
  Upload,
  Users,
  X,
} from "lucide-react";
import { api, errorMessage, text, today } from "./api";
import {
  CustomerForm,
  DocumentForm,
  OpportunityForm,
  PaymentForm,
  ProductForm,
  TaskForm,
  UserForm,
} from "./forms";
import { Badge, Empty, Field, Modal, Stat } from "./ui";
import type {
  AccountingInfo,
  Activity,
  Approval,
  Customer,
  Dashboard,
  DocumentKind,
  Ledger,
  Opportunity,
  Payment,
  Product,
  Report,
  SalesDocument,
  Settings,
  Store,
  Task,
  User,
} from "./types";

type Page =
  | "Dashboard"
  | "Customers"
  | "Sales pipeline"
  | "Follow-ups"
  | "Quotes"
  | "Approvals"
  | "Invoices"
  | "Customer accounts"
  | "Accounting"
  | "Reports"
  | "Catalogue"
  | "Settings";
type Dialog =
  | { type: "customer"; customer?: Customer }
  | { type: "document"; kind: DocumentKind; document?: SalesDocument }
  | { type: "opportunity"; existing?: Opportunity }
  | { type: "payment"; payment?: Payment }
  | { type: "task" }
  | { type: "product"; product?: Product }
  | { type: "user" }
  | { type: "approval"; approval: Approval }
  | { type: "viewDocument"; document: SalesDocument }
  | { type: "viewCustomer"; customer: Customer };
const nav: { name: Page; icon: typeof LayoutDashboard; group?: string }[] = [
  { name: "Dashboard", icon: LayoutDashboard, group: "WORKSPACE" },
  { name: "Customers", icon: Users },
  { name: "Sales pipeline", icon: BriefcaseBusiness },
  { name: "Follow-ups", icon: ListTodo },
  { name: "Quotes", icon: FileText, group: "SALES & FINANCE" },
  { name: "Approvals", icon: ShieldCheck },
  { name: "Invoices", icon: FileCheck2 },
  { name: "Customer accounts", icon: BookOpen },
  { name: "Accounting", icon: Building2 },
  { name: "Reports", icon: BarChart3 },
  { name: "Catalogue", icon: Package, group: "MANAGEMENT" },
  { name: "Settings", icon: Settings2 },
];
const descriptions: Record<Page, string> = {
  Dashboard: "A clear view of your relationships, sales and finances.",
  Customers: "Every relationship, organised and connected.",
  "Sales pipeline": "Turn the next conversation into your next opportunity.",
  "Follow-ups": "Keep promises. Stay one step ahead.",
  Quotes: "Build proposals that move business forward.",
  Approvals: "Review, decide and keep your team moving.",
  Invoices: "From completed work to collected revenue.",
  "Customer accounts": "A complete picture of every customer balance.",
  Accounting: "Your organization's general ledger, inside CRM.",
  Reports: "The numbers behind your business decisions.",
  Catalogue: "Your products and services, ready for the next quote.",
  Settings: "Make this workspace work for your business.",
};
const tone = (status: string) =>
  ["approved", "issued", "accepted", "paid", "Won"].includes(status)
    ? "green"
    : ["pending", "partial", "Negotiation"].includes(status)
      ? "amber"
      : ["rejected", "declined", "overdue", "Lost"].includes(status)
        ? "red"
        : ["sent", "Proposal"].includes(status)
          ? "blue"
          : "";
function initials(name: string) {
  return name
    .split(" ")
    .slice(0, 2)
    .map((value) => value[0])
    .join("")
    .toUpperCase();
}
function displayDate(value: string) {
  if (!value) return "—";
  return new Date(`${value.slice(0, 10)}T12:00:00`).toLocaleDateString(
    "en-GB",
    { day: "numeric", month: "short", year: "numeric" },
  );
}
function Table({
  headers,
  children,
}: {
  headers: string[];
  children: ReactNode;
}) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {headers.map((header) => (
              <th key={header}>{header}</th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [store, setStore] = useState<Store | null>(null);
  const [booting, setBooting] = useState(true);
  const [page, setPage] = useState<Page>("Dashboard");
  const [search, setSearch] = useState("");
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const [notice, setNotice] = useState<{
    message: string;
    error: boolean;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [mobile, setMobile] = useState(false);
  useEffect(() => {
    const expire = () => {
      setUser(null);
      setStore(null);
      setDialog(null);
      setPage("Dashboard");
      setSearch("");
    };
    window.addEventListener("crm-session-expired", expire);
    return () => window.removeEventListener("crm-session-expired", expire);
  }, []);
  const load = useCallback(async (member: User) => {
    const [
      customers,
      products,
      opportunities,
      tasks,
      documents,
      approvals,
      users,
      payments,
      settings,
      dashboard,
    ] = await Promise.all([
      api<Customer[]>("/customers"),
      api<Product[]>("/products"),
      api<Opportunity[]>("/opportunities"),
      api<Task[]>("/tasks"),
      api<SalesDocument[]>("/documents"),
      api<Approval[]>("/approvals"),
      api<User[]>("/users"),
      member.role !== "sales"
        ? api<Payment[]>("/payments")
        : Promise.resolve([] as Payment[]),
      Promise.all([
        api<Settings>("/settings"),
        api<Pick<Settings, "hosted" | "manual_ledger_kinds">>("/workspace"),
      ]).then(([values, mode]) => ({ ...values, ...mode })),
      api<Dashboard>("/dashboard"),
    ]);
    setStore({
      customers,
      products,
      opportunities,
      tasks,
      documents,
      approvals,
      users,
      payments,
      settings,
      dashboard,
    });
  }, []);
  useEffect(() => {
    let live = true;
    api<User>("/auth/me")
      .then(async (member) => {
        if (!live) return;
        setUser(member);
        await load(member);
      })
      .catch(() => {})
      .finally(() => {
        if (live) setBooting(false);
      });
    return () => {
      live = false;
    };
  }, [load]);
  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), 6000);
    return () => window.clearTimeout(timer);
  }, [notice]);
  const refresh = useCallback(async () => {
    if (user) await load(user);
  }, [load, user]);
  const close = useCallback(() => setDialog(null), []);
  async function act(path: string, method = "POST", body?: object) {
    if (busy) return;
    setBusy(true);
    try {
      await api(path, method, body);
      await refresh();
      setNotice({ message: "Your changes have been saved.", error: false });
    } catch (error) {
      setNotice({ message: errorMessage(error), error: true });
    } finally {
      setBusy(false);
    }
  }
  function navigate(next: Page) {
    setPage(next);
    setSearch("");
    setMobile(false);
  }
  if (booting)
    return (
      <div className="loading-screen">
        <Loader2 className="spin" />
        <span>Opening your workspace…</span>
      </div>
    );
  if (!user)
    return (
      <Login
        onLogin={async (member) => {
          setUser(member);
          await load(member);
        }}
      />
    );
  if (!store)
    return (
      <div className="loading-screen">
        <h2>Workspace could not be loaded</h2>
        <button
          className="button"
          onClick={() =>
            void refresh().catch((error) =>
              setNotice({ message: errorMessage(error), error: true }),
            )
          }
        >
          Try again
        </button>
        {notice?.message}
      </div>
    );
  const props = { store, user, close, refresh };
  const canSell = user.role !== "finance";
  const canFinance = user.role !== "sales";
  const canManage = ["admin", "manager"].includes(user.role);
  const filtered = (values: string[]) =>
    values.join(" ").toLowerCase().includes(search.toLowerCase());
  const fmt = (amount: string | number) =>
    `${store.settings.currency} ${Number(amount).toLocaleString("en-US", { minimumFractionDigits: store.settings.decimals, maximumFractionDigits: store.settings.decimals })}`;
  const clientName = (id: number | null) =>
    store.customers.find((value) => value.id === id)?.name ?? "Unlinked lead";
  const userName = (id: number) =>
    store.users.find((value) => value.id === id)?.name ?? "Team member";
  const documentAction = async (document: SalesDocument, action: string) => {
    if (busy) return;
    setBusy(true);
    try {
      const result = await api<SalesDocument>(
        `/documents/${document.id}/actions`,
        "POST",
        { action },
      );
      await refresh();
      if (action === "convert") {
        navigate("Invoices");
        setDialog({ type: "viewDocument", document: result });
      } else setDialog(null);
      setNotice({
        message: `${document.number}: ${action === "submit" ? "submitted" : action === "send" ? "marked as sent" : action === "accept" ? "acceptance recorded" : action === "convert" ? "invoice draft created" : action === "decline" ? "decline recorded" : "cancelled"}.`,
        error: false,
      });
    } catch (error) {
      setNotice({ message: errorMessage(error), error: true });
    } finally {
      setBusy(false);
    }
  };
  const selectedDocument =
    dialog &&
    (dialog.type === "viewDocument" || dialog.type === "document") &&
    dialog.document
      ? (store.documents.find((value) => value.id === dialog.document?.id) ??
        dialog.document)
      : undefined;
  return (
    <div className="app-shell">
      <aside className={`sidebar ${mobile ? "open" : ""}`}>
        <div className="brand">
          <div className="brand-symbol">
            n<span>.</span>
          </div>
          <div>
            <strong>nexera</strong>
            <small>BUSINESS CRM</small>
          </div>
          <button
            className="icon-button mobile-close"
            aria-label="Close navigation"
            onClick={() => setMobile(false)}
          >
            <X size={20} />
          </button>
        </div>
        <div className="workspace-switch">
          <span className="workspace-avatar">
            <Building2 size={18} />
          </span>
          <div>
            <strong>
              {store.settings.company.replace(" · Demo workspace", "")}
            </strong>
            <small>
              {store.settings.company.includes("Demo")
                ? "Demo workspace"
                : "Business workspace"}
            </small>
          </div>
        </div>
        <nav>
          {nav
            .filter(
              (item) =>
                canFinance || !["Reports", "Accounting"].includes(item.name),
            )
            .map((item) => (
              <div key={item.name}>
                {item.group && <div className="nav-group">{item.group}</div>}
                <button
                  className={`nav-item ${page === item.name ? "active" : ""}`}
                  onClick={() => navigate(item.name)}
                >
                  <item.icon size={18} />
                  <span>{item.name}</span>
                  {item.name === "Approvals" &&
                    store.dashboard.pending_approvals > 0 && (
                      <b>{store.dashboard.pending_approvals}</b>
                    )}
                  {item.name === "Follow-ups" &&
                    store.dashboard.overdue_tasks > 0 && (
                      <span className="nav-dot" />
                    )}
                </button>
              </div>
            ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="sidebar-tip">
            <span className="tip-icon">
              <TrendingUp size={18} />
            </span>
            <strong>
              Good relationships.
              <br />
              Better business.
            </strong>
            <p>Your next opportunity starts with a follow-up.</p>
          </div>
          <button
            className="profile"
            title="Sign out"
            onClick={() => {
              void api("/auth/logout", "POST")
                .then(() => {
                  setUser(null);
                  setStore(null);
                  navigate("Dashboard");
                })
                .catch((error) =>
                  setNotice({ message: errorMessage(error), error: true }),
                );
            }}
          >
            <span className="avatar">{initials(user.name)}</span>
            <span>
              <strong>{user.name}</strong>
              <small>{user.role}</small>
            </span>
            <LogOut size={17} />
          </button>
        </div>
      </aside>
      {mobile && (
        <div className="sidebar-scrim" onClick={() => setMobile(false)} />
      )}
      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumb">
            <button
              className="icon-button mobile-menu"
              aria-label="Open navigation"
              onClick={() => setMobile(true)}
            >
              <Menu size={21} />
            </button>
            <span>Workspace</span>
            <ChevronRight size={14} />
            <strong>{page}</strong>
          </div>
          <div className="topbar-actions">
            <div className="top-search">
              <Search size={16} />
              <input
                aria-label="Search current page"
                placeholder="Search this page…"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
              <kbd>⌕</kbd>
            </div>
            <button
              className="icon-button"
              aria-label="Refresh workspace"
              disabled={busy}
              onClick={() =>
                void refresh().catch((error) =>
                  setNotice({ message: errorMessage(error), error: true }),
                )
              }
            >
              <RefreshCw size={18} />
            </button>
            <button
              className="icon-button notification-button"
              aria-label="View overdue follow-ups"
              onClick={() => navigate("Follow-ups")}
            >
              <Bell size={19} />
              {store.dashboard.overdue_tasks > 0 && <i />}
            </button>
            <span className="avatar top-avatar">{initials(user.name)}</span>
          </div>
        </header>
        <main>
          <div className="page-heading">
            <div>
              <div className="eyebrow">YOUR BUSINESS, CONNECTED</div>
              <h1>
                {page === "Dashboard"
                  ? `Welcome back, ${user.name.split(" ")[0]}`
                  : page}
                <span className="heading-dot">.</span>
              </h1>
              <p>{descriptions[page]}</p>
            </div>
            <div className="heading-actions">
              {page === "Dashboard" && (
                <span className="date-label">
                  <Clock3 size={15} />
                  {displayDate(today())}
                </span>
              )}
              {page === "Customers" && canSell && (
                <button
                  className="button"
                  onClick={() => setDialog({ type: "customer" })}
                >
                  <Plus size={17} />
                  New customer
                </button>
              )}
              {page === "Sales pipeline" && canSell && (
                <button
                  className="button"
                  onClick={() => setDialog({ type: "opportunity" })}
                >
                  <Plus size={17} />
                  New opportunity
                </button>
              )}
              {page === "Follow-ups" && canSell && (
                <button
                  className="button"
                  onClick={() => setDialog({ type: "task" })}
                >
                  <Plus size={17} />
                  Schedule follow-up
                </button>
              )}
              {["Dashboard", "Quotes"].includes(page) && canSell && (
                <button
                  className="button"
                  onClick={() => setDialog({ type: "document", kind: "quote" })}
                >
                  <Plus size={17} />
                  New quote
                </button>
              )}
              {page === "Invoices" && canFinance && (
                <button
                  className="button"
                  onClick={() =>
                    setDialog({ type: "document", kind: "invoice" })
                  }
                >
                  <Plus size={17} />
                  New invoice
                </button>
              )}
              {page === "Customer accounts" && canFinance && (
                <button
                  className="button"
                  onClick={() => setDialog({ type: "payment" })}
                >
                  <Plus size={17} />
                  Record payment
                </button>
              )}
              {page === "Catalogue" && canManage && (
                <button
                  className="button"
                  onClick={() => setDialog({ type: "product" })}
                >
                  <Plus size={17} />
                  New item
                </button>
              )}
            </div>
          </div>
          {page === "Dashboard" && (
            <>
              <div className="stats-grid">
                <Stat
                  label="Active pipeline"
                  value={fmt(store.dashboard.pipeline)}
                  hint={`${store.opportunities.filter((value) => !["Won", "Lost"].includes(value.stage)).length} open opportunities`}
                  icon={<TrendingUp size={19} />}
                />
                <Stat
                  label="Customer relationships"
                  value={String(store.dashboard.customers)}
                  hint="One connected customer directory"
                  icon={<Users size={19} />}
                />
                <Stat
                  label={
                    canFinance ? "Outstanding invoices" : "Overdue follow-ups"
                  }
                  value={
                    canFinance
                      ? fmt(store.dashboard.financial?.outstanding ?? "0")
                      : String(store.dashboard.overdue_tasks)
                  }
                  hint={
                    canFinance
                      ? "Live balance after payments and credits"
                      : "Follow-ups that need your attention"
                  }
                  icon={<CircleDollarSign size={19} />}
                />
                <Stat
                  label="Awaiting approval"
                  value={String(store.dashboard.pending_approvals)}
                  hint="Keep your sales and finance moving"
                  icon={<ShieldCheck size={19} />}
                />
              </div>
              <div className="dashboard-grid">
                <section className="panel revenue-panel">
                  <div className="panel-heading">
                    <div>
                      <h2>Revenue & collections</h2>
                      <p>Net invoiced sales and recorded payments</p>
                    </div>
                    <Badge>All time</Badge>
                  </div>
                  {store.dashboard.financial ? (
                    <RevenueChart
                      report={store.dashboard.financial}
                      fmt={fmt}
                    />
                  ) : (
                    <div className="sales-summary">
                      <TrendingUp size={40} />
                      <h3>
                        Your pipeline is worth {fmt(store.dashboard.pipeline)}
                      </h3>
                      <button
                        className="text-button"
                        onClick={() => navigate("Sales pipeline")}
                      >
                        View your opportunities <ArrowRight size={16} />
                      </button>
                    </div>
                  )}
                </section>
                <section className="panel">
                  <div className="panel-heading">
                    <div>
                      <h2>Your next moves</h2>
                      <p>A little follow-up goes a long way</p>
                    </div>
                    <button
                      className="icon-button"
                      aria-label="Add follow-up"
                      disabled={!canSell}
                      onClick={() => setDialog({ type: "task" })}
                    >
                      <Plus size={19} />
                    </button>
                  </div>
                  <div className="follow-list">
                    {store.tasks
                      .filter(
                        (value) => !value.done && value.owner_id === user.id,
                      )
                      .slice(0, 4)
                      .map((task) => (
                        <div className="follow-item" key={task.id}>
                          <button
                            className="task-check"
                            aria-label={`Complete ${task.title}`}
                            disabled={busy || !canSell}
                            onClick={() => void act(`/tasks/${task.id}/toggle`)}
                          />
                          <div>
                            <strong>{task.title}</strong>
                            <small>{clientName(task.customer_id)}</small>
                          </div>
                          <Badge tone={task.due < today() ? "red" : ""}>
                            {task.due < today()
                              ? "Overdue"
                              : task.due === today()
                                ? "Today"
                                : displayDate(task.due)}
                          </Badge>
                        </div>
                      ))}
                    {!store.tasks.some(
                      (value) => !value.done && value.owner_id === user.id,
                    ) && (
                      <Empty
                        title="You’re all caught up"
                        detail="No open follow-ups assigned to you."
                      />
                    )}
                  </div>
                  <button
                    className="panel-footer"
                    onClick={() => navigate("Follow-ups")}
                  >
                    View all follow-ups <ArrowRight size={15} />
                  </button>
                </section>
                <section className="panel quotes-panel">
                  <div className="panel-heading">
                    <div>
                      <h2>Recent quotes</h2>
                      <p>From the first proposal to the next win</p>
                    </div>
                    <button
                      className="text-button"
                      onClick={() => navigate("Quotes")}
                    >
                      View all <ArrowUpRight size={15} />
                    </button>
                  </div>
                  <DocumentTable
                    values={store.documents
                      .filter(
                        (value) =>
                          value.kind === "quote" &&
                          filtered([value.number, value.customer]),
                      )
                      .slice(0, 5)}
                    fmt={fmt}
                    open={(document) =>
                      setDialog({ type: "viewDocument", document })
                    }
                  />
                </section>
                <section className="panel">
                  <div className="panel-heading">
                    <div>
                      <h2>Workspace activity</h2>
                      <p>A shared view of what’s happening</p>
                    </div>
                    <span className="live-label">
                      <i />
                      Live
                    </span>
                  </div>
                  <div className="activity-list">
                    {store.dashboard.activities
                      .filter((value) => filtered([value.action, value.detail]))
                      .slice(0, 5)
                      .map((activity) => (
                        <div className="activity-item" key={activity.id}>
                          <span className="activity-dot" />
                          <div>
                            <strong>{activity.action}</strong>
                            <p>
                              {activity.detail || userName(activity.actor_id)}
                            </p>
                            <small>{displayDate(activity.created)}</small>
                          </div>
                        </div>
                      ))}
                  </div>
                </section>
              </div>
            </>
          )}
          {page === "Customers" && (
            <section className="panel">
              <div className="panel-heading">
                <div className="list-title">
                  <h2>Customer directory</h2>
                  <Badge>{store.customers.length} customers</Badge>
                </div>
                <div className="toolbar">
                  <a
                    className="button secondary small"
                    href="/api/exports/customers"
                  >
                    <Download size={15} />
                    Export
                  </a>
                  {canSell && (
                    <label className="button secondary small">
                      <Upload size={15} />
                      Import CSV
                      <input
                        hidden
                        type="file"
                        accept=".csv"
                        onChange={(event) => {
                          const file = event.target.files?.[0];
                          if (!file) return;
                          const form = new FormData();
                          form.set("file", file);
                          setBusy(true);
                          void api<{ imported: number }>(
                            "/imports/customers",
                            "POST",
                            form,
                          )
                            .then(async (result) => {
                              await refresh();
                              setNotice({
                                message: `${result.imported} customers imported.`,
                                error: false,
                              });
                            })
                            .catch((error) =>
                              setNotice({
                                message: errorMessage(error),
                                error: true,
                              }),
                            )
                            .finally(() => setBusy(false));
                          event.target.value = "";
                        }}
                      />
                    </label>
                  )}
                </div>
              </div>
              {store.customers.length ? (
                <Table
                  headers={[
                    "Customer",
                    "Contact",
                    "Tags",
                    "Account balance",
                    "",
                  ]}
                >
                  {store.customers
                    .filter((value) =>
                      filtered([
                        value.name,
                        value.email,
                        value.contact,
                        value.phone,
                        value.tags,
                      ]),
                    )
                    .map((customer, index) => (
                      <tr key={customer.id}>
                        <td>
                          <button
                            className="customer-cell"
                            onClick={() =>
                              setDialog({ type: "viewCustomer", customer })
                            }
                          >
                            <span
                              className={`customer-avatar color-${index % 4}`}
                            >
                              {initials(customer.name)}
                            </span>
                            <span>
                              <strong>{customer.name}</strong>
                              <small>{customer.email || customer.kind}</small>
                            </span>
                          </button>
                        </td>
                        <td>
                          <strong className="cell-main">
                            {customer.contact || "—"}
                          </strong>
                          <small>{customer.phone || "No phone recorded"}</small>
                        </td>
                        <td>
                          <div className="tags">
                            {customer.tags
                              .split(",")
                              .filter(Boolean)
                              .slice(0, 2)
                              .map((tag) => (
                                <Badge key={tag}>{tag}</Badge>
                              ))}
                          </div>
                        </td>
                        <td>
                          <strong className="amount">
                            {fmt(customer.balance)}
                          </strong>
                          {Number(customer.balance) < 0 && (
                            <small>Customer credit</small>
                          )}
                        </td>
                        <td>
                          <button
                            className="icon-button"
                            aria-label={`View ${customer.name}`}
                            onClick={() =>
                              setDialog({ type: "viewCustomer", customer })
                            }
                          >
                            <ArrowUpRight size={18} />
                          </button>
                        </td>
                      </tr>
                    ))}
                </Table>
              ) : (
                <Empty title="Your customer directory starts here" />
              )}
            </section>
          )}
          {page === "Sales pipeline" && (
            <>
              <div className="pipeline-summary">
                <span>
                  <i className="green-dot" />
                  {store.opportunities.length} opportunities
                </span>
                <strong>
                  {fmt(store.dashboard.pipeline)}{" "}
                  <small>open pipeline value</small>
                </strong>
              </div>
              <div className="pipeline-board">
                {[
                  "New",
                  "Qualified",
                  "Proposal",
                  "Negotiation",
                  "Won",
                  "Lost",
                ].map((stage, stageIndex) => {
                  const values = store.opportunities.filter(
                    (value) =>
                      value.stage === stage &&
                      filtered([
                        value.title,
                        clientName(value.customer_id),
                        value.source,
                      ]),
                  );
                  return (
                    <section
                      className={`pipeline-column stage-${stageIndex}`}
                      key={stage}
                    >
                      <header>
                        <div>
                          <span className="stage-dot" />
                          <h3>{stage}</h3>
                          <Badge>{values.length}</Badge>
                        </div>
                        <strong>
                          {fmt(
                            values.reduce(
                              (sum, value) => sum + Number(value.value),
                              0,
                            ),
                          )}
                        </strong>
                      </header>
                      {values.map((value) => (
                        <article className="deal-card" key={value.id}>
                          <button
                            className="deal-title"
                            onClick={() =>
                              setDialog({
                                type: "opportunity",
                                existing: value,
                              })
                            }
                            disabled={!canSell}
                          >
                            {value.title}
                          </button>
                          <p>{clientName(value.customer_id)}</p>
                          <strong>{fmt(value.value)}</strong>
                          <div className="deal-meta">
                            <Badge>{value.source}</Badge>
                            <span
                              className="mini-avatar"
                              title={userName(value.owner_id)}
                            >
                              {initials(userName(value.owner_id))}
                            </span>
                          </div>
                          <footer>
                            <Clock3 size={13} />
                            {value.closing_date
                              ? displayDate(value.closing_date)
                              : "No closing date"}
                          </footer>
                          {canSell && (
                            <select
                              aria-label={`Move ${value.title} to stage`}
                              value={value.stage}
                              disabled={busy}
                              onChange={(event) => {
                                const { id, ...body } = value;
                                void act(`/opportunities/${id}`, "PUT", {
                                  ...body,
                                  closing_date: body.closing_date || null,
                                  stage: event.target.value,
                                });
                              }}
                            >
                              {[
                                "New",
                                "Qualified",
                                "Proposal",
                                "Negotiation",
                                "Won",
                                "Lost",
                              ].map((item) => (
                                <option key={item}>{item}</option>
                              ))}
                            </select>
                          )}
                        </article>
                      ))}
                      {!values.length && (
                        <div className="pipeline-empty">No opportunities</div>
                      )}
                    </section>
                  );
                })}
              </div>
            </>
          )}
          {page === "Follow-ups" && (
            <section className="panel">
              <div className="panel-heading">
                <h2>Follow-up planner</h2>
                <Badge>
                  {store.tasks.filter((value) => !value.done).length} open tasks
                </Badge>
              </div>
              {store.tasks.length ? (
                <Table
                  headers={[
                    "Task",
                    "Customer",
                    "Assigned to",
                    "Due date",
                    "Status",
                  ]}
                >
                  {store.tasks
                    .filter((value) =>
                      filtered([
                        value.title,
                        clientName(value.customer_id),
                        userName(value.owner_id),
                      ]),
                    )
                    .map((task) => (
                      <tr key={task.id}>
                        <td>
                          <div className="task-title">
                            <button
                              className={`task-check ${task.done ? "done" : ""}`}
                              disabled={busy || !canSell}
                              aria-label={`${task.done ? "Reopen" : "Complete"} ${task.title}`}
                              onClick={() =>
                                void act(`/tasks/${task.id}/toggle`)
                              }
                            >
                              {task.done && <Check size={13} />}
                            </button>
                            <span>
                              <strong>{task.title}</strong>
                              <small>{task.notes}</small>
                            </span>
                          </div>
                        </td>
                        <td>{clientName(task.customer_id)}</td>
                        <td>{userName(task.owner_id)}</td>
                        <td>{displayDate(task.due)}</td>
                        <td>
                          <Badge
                            tone={
                              task.done
                                ? "green"
                                : task.due < today()
                                  ? "red"
                                  : ""
                            }
                          >
                            {task.done
                              ? "Completed"
                              : task.due < today()
                                ? "Overdue"
                                : "Scheduled"}
                          </Badge>
                        </td>
                      </tr>
                    ))}
                </Table>
              ) : (
                <Empty
                  title="Make time for your customers"
                  detail="Schedule a call, meeting or reminder."
                />
              )}
            </section>
          )}
          {["Quotes", "Invoices"].includes(page) && (
            <section className="panel">
              <div className="panel-heading">
                <div className="list-title">
                  <h2>
                    {page === "Quotes" ? "All quotations" : "Invoice register"}
                  </h2>
                  <Badge>
                    {
                      store.documents.filter(
                        (value) =>
                          value.kind ===
                          (page === "Quotes" ? "quote" : "invoice"),
                      ).length
                    }{" "}
                    documents
                  </Badge>
                </div>
                <span className="muted small-text">
                  Drafts remain outside the ledger
                </span>
              </div>
              <DocumentTable
                values={store.documents.filter(
                  (value) =>
                    value.kind === (page === "Quotes" ? "quote" : "invoice") &&
                    filtered([
                      value.number,
                      value.customer,
                      value.status,
                      value.payment_state,
                    ]),
                )}
                fmt={fmt}
                open={(document) =>
                  setDialog({ type: "viewDocument", document })
                }
                invoices={page === "Invoices"}
              />
            </section>
          )}
          {page === "Approvals" && (
            <section className="panel">
              <div className="panel-heading">
                <h2>Approval requests</h2>
                <Badge tone="amber">
                  {
                    store.approvals.filter(
                      (value) => value.status === "pending",
                    ).length
                  }{" "}
                  pending
                </Badge>
              </div>
              <div className="info-strip">
                <ShieldCheck size={17} />A separate reviewer approves financial
                documents. Document authors cannot approve their own requests.
              </div>
              {store.approvals.length ? (
                <Table
                  headers={[
                    "Document",
                    "Customer",
                    "Requested by",
                    "Amount",
                    "Status",
                    "",
                  ]}
                >
                  {store.approvals
                    .filter((value) =>
                      filtered([
                        value.document.number,
                        value.document.customer,
                        value.requester,
                        value.status,
                      ]),
                    )
                    .map((approval) => (
                      <tr key={approval.id}>
                        <td>
                          <button
                            className="text-button"
                            onClick={() =>
                              setDialog({
                                type: "viewDocument",
                                document: approval.document,
                              })
                            }
                          >
                            {approval.document.number}
                          </button>
                          <small>
                            {approval.document.kind} · revision{" "}
                            {approval.revision}
                          </small>
                        </td>
                        <td>{approval.document.customer}</td>
                        <td>{approval.requester}</td>
                        <td className="amount">
                          {fmt(approval.document.total)}
                        </td>
                        <td>
                          <Badge tone={tone(approval.status)}>
                            {approval.status}
                          </Badge>
                          <small>{approval.comment}</small>
                        </td>
                        <td>
                          {approval.status === "pending" &&
                          canManage &&
                          approval.document.author_id !== user.id &&
                          approval.requested_by !== user.id ? (
                            <button
                              className="button secondary small"
                              onClick={() =>
                                setDialog({ type: "approval", approval })
                              }
                            >
                              Review <ArrowRight size={14} />
                            </button>
                          ) : approval.status === "pending" &&
                            (approval.document.author_id === user.id ||
                              approval.requested_by === user.id) ? (
                            <span className="muted small-text">
                              Needs another reviewer
                            </span>
                          ) : null}
                        </td>
                      </tr>
                    ))}
                </Table>
              ) : (
                <Empty title="No requests to review" />
              )}
            </section>
          )}
          {page === "Customer accounts" && (
            <>
              <div className="account-actions">
                {canFinance && (
                  <>
                    <button
                      className="button secondary"
                      onClick={() =>
                        setDialog({ type: "document", kind: "credit" })
                      }
                    >
                      Credit note
                    </button>
                    {!store.settings.manual_ledger_kinds?.includes(
                      "opening",
                    ) && (
                      <button
                        className="button secondary"
                        onClick={() =>
                          setDialog({ type: "document", kind: "opening" })
                        }
                      >
                        Opening balance
                      </button>
                    )}
                    {!store.settings.manual_ledger_kinds?.includes(
                      "adjustment",
                    ) && (
                      <button
                        className="button secondary"
                        onClick={() =>
                          setDialog({ type: "document", kind: "adjustment" })
                        }
                      >
                        Adjustment
                      </button>
                    )}
                    <button
                      className="button secondary"
                      onClick={() =>
                        setDialog({ type: "document", kind: "refund" })
                      }
                    >
                      Refund
                    </button>
                  </>
                )}
              </div>
              <section className="panel">
                <div className="panel-heading">
                  <h2>Customer account balances</h2>
                  <p>
                    Positive balances are receivables; negative balances are
                    customer credit.
                  </p>
                </div>
                <Table
                  headers={[
                    "Customer",
                    "Ledger balance",
                    "Unallocated payments",
                    "Statement",
                  ]}
                >
                  {store.customers
                    .filter((value) => filtered([value.name, value.contact]))
                    .map((value) => (
                      <tr key={value.id}>
                        <td>
                          <button
                            className="text-button"
                            onClick={() =>
                              setDialog({
                                type: "viewCustomer",
                                customer: value,
                              })
                            }
                          >
                            {value.name}
                          </button>
                        </td>
                        <td className="amount">{fmt(value.balance)}</td>
                        <td>{fmt(value.unallocated)}</td>
                        <td>
                          <div className="row-actions">
                            <a
                              className="text-button"
                              href={`/api/customers/${value.id}/statement`}
                            >
                              <Download size={14} />
                              PDF
                            </a>
                            <a
                              className="text-button"
                              href={`/api/customers/${value.id}/statement?format=csv`}
                            >
                              CSV
                            </a>
                          </div>
                        </td>
                      </tr>
                    ))}
                </Table>
              </section>
              {canFinance && (
                <section className="panel space-top">
                  <div className="panel-heading">
                    <h2>Payment register</h2>
                    <Badge>{store.payments.length} receipts</Badge>
                  </div>
                  {store.payments.length ? (
                    <Table
                      headers={[
                        "Reference",
                        "Customer",
                        "Date",
                        "Method",
                        "Amount",
                        "",
                      ]}
                    >
                      {store.payments
                        .filter((value) =>
                          filtered([
                            value.reference,
                            clientName(value.customer_id),
                            value.method,
                          ]),
                        )
                        .map((value) => (
                          <tr key={value.id}>
                            <td>
                              <strong>{value.reference}</strong>
                              <AccountingBadge info={value.accounting} />
                            </td>
                            <td>{clientName(value.customer_id)}</td>
                            <td>{displayDate(value.date)}</td>
                            <td>{value.method}</td>
                            <td className="amount">{fmt(value.amount)}</td>
                            <td>
                              {Number(value.unallocated) > 0 && (
                                <button
                                  className="text-button"
                                  onClick={() =>
                                    setDialog({
                                      type: "payment",
                                      payment: value,
                                    })
                                  }
                                >
                                  Allocate credit <ArrowRight size={14} />
                                </button>
                              )}
                            </td>
                          </tr>
                        ))}
                    </Table>
                  ) : (
                    <Empty title="No payments recorded" />
                  )}
                </section>
              )}
              <section className="panel space-top">
                <div className="panel-heading">
                  <h2>Account transactions</h2>
                  <p>
                    {store.settings.manual_ledger_kinds?.length
                      ? "Credit notes and refunds. Opening balances and adjustments are recorded by your accountant in the ERP."
                      : "Credit notes, opening balances, adjustments and refunds"}
                  </p>
                </div>
                <DocumentTable
                  values={store.documents.filter(
                    (value) =>
                      !["quote", "invoice"].includes(value.kind) &&
                      filtered([value.number, value.customer, value.reason]),
                  )}
                  fmt={fmt}
                  open={(document) =>
                    setDialog({ type: "viewDocument", document })
                  }
                />
              </section>
            </>
          )}
          {page === "Reports" && (
            <ReportsPage
              fmt={fmt}
              onError={(message) => setNotice({ message, error: true })}
            />
          )}
          {page === "Accounting" && canFinance && (
            <AccountingPage
              fmt={fmt}
              canActivate={user.role === "admin"}
              canRetry={["admin", "finance"].includes(user.role)}
            />
          )}
          {page === "Catalogue" && (
            <section className="panel">
              <div className="panel-heading">
                <h2>Products & services</h2>
                <Badge>{store.products.length} items</Badge>
              </div>
              {store.products.length ? (
                <Table
                  headers={["Product / service", "Unit", "Price", "Status", ""]}
                >
                  {store.products
                    .filter((value) =>
                      filtered([value.name, value.description]),
                    )
                    .map((product) => (
                      <tr key={product.id}>
                        <td>
                          <strong>{product.name}</strong>
                          <small>{product.description}</small>
                        </td>
                        <td>{product.unit}</td>
                        <td className="amount">{fmt(product.price)}</td>
                        <td>
                          <Badge tone={product.active ? "green" : ""}>
                            {product.active ? "Active" : "Archived"}
                          </Badge>
                        </td>
                        <td>
                          {canManage && (
                            <button
                              className="text-button"
                              onClick={() =>
                                setDialog({ type: "product", product })
                              }
                            >
                              Edit <ArrowUpRight size={15} />
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                </Table>
              ) : (
                <Empty
                  title="Build your catalogue"
                  detail="Reuse products and services when preparing quotes."
                />
              )}
            </section>
          )}
          {page === "Settings" && (
            <SettingsPage
              store={store}
              user={user}
              refresh={refresh}
              addUser={() => setDialog({ type: "user" })}
              toggleUser={(id) => void act(`/users/${id}/toggle`)}
              busy={busy}
            />
          )}
          <footer className="main-footer">
            <span>
              nexera <b>CRM</b>
            </span>
            <span>
              {store.settings.company.includes("Demo")
                ? "Demo workspace · All sample records are fictional"
                : "Your business, connected."}
            </span>
          </footer>
        </main>
      </div>
      {notice && (
        <div
          className={`toast ${notice.error ? "error-toast" : ""}`}
          role="status"
        >
          {notice.error ? <X size={18} /> : <CheckCircle2 size={18} />}
          <span>{notice.message}</span>
          <button
            className="icon-button"
            aria-label="Dismiss notification"
            onClick={() => setNotice(null)}
          >
            <X size={15} />
          </button>
        </div>
      )}
      {dialog?.type === "customer" && (
        <CustomerForm {...props} customer={dialog.customer} />
      )}
      {dialog?.type === "document" && (
        <DocumentForm
          {...props}
          kind={dialog.kind}
          existing={selectedDocument}
        />
      )}
      {dialog?.type === "opportunity" && (
        <OpportunityForm {...props} existing={dialog.existing} />
      )}
      {dialog?.type === "payment" && (
        <PaymentForm {...props} payment={dialog.payment} />
      )}
      {dialog?.type === "task" && <TaskForm {...props} />}
      {dialog?.type === "product" && (
        <ProductForm {...props} existing={dialog.product} />
      )}
      {dialog?.type === "user" && <UserForm {...props} />}
      {dialog?.type === "approval" && (
        <ApprovalDialog
          approval={dialog.approval}
          close={close}
          refresh={refresh}
          fmt={fmt}
        />
      )}
      {dialog?.type === "viewDocument" && selectedDocument && (
        <Modal
          title={selectedDocument.number}
          subtitle={`${selectedDocument.customer} · ${selectedDocument.kind} · revision ${selectedDocument.revision}`}
          close={close}
          wide
        >
          <div className="document-details">
            <div className="document-meta">
              <Badge tone={tone(selectedDocument.status)}>
                {selectedDocument.status}
              </Badge>
              <span>
                {displayDate(selectedDocument.date)} →{" "}
                {displayDate(selectedDocument.due)}
              </span>
              <a
                className="button secondary small"
                href={`/api/documents/${selectedDocument.id}/pdf`}
              >
                <Download size={15} />
                Download PDF
              </a>
            </div>
            {selectedDocument.items.length > 0 && (
              <Table headers={["Description", "Quantity", "Unit price"]}>
                {selectedDocument.items.map((item, i) => (
                  <tr key={i}>
                    <td>{item.description}</td>
                    <td>{item.quantity}</td>
                    <td>{fmt(item.unit_price)}</td>
                  </tr>
                ))}
              </Table>
            )}
            <div className="totals">
              <div>
                <span>Subtotal</span>
                <strong>{fmt(selectedDocument.subtotal)}</strong>
              </div>
              <div>
                <span>Discount ({selectedDocument.discount_rate}%)</span>
                <strong>{fmt(selectedDocument.discount)}</strong>
              </div>
              <div>
                <span>Tax ({selectedDocument.tax_rate}%)</span>
                <strong>{fmt(selectedDocument.tax)}</strong>
              </div>
              <div className="total">
                <span>Total</span>
                <strong>{fmt(selectedDocument.total)}</strong>
              </div>
              {selectedDocument.kind === "invoice" &&
                selectedDocument.status === "issued" && (
                  <div>
                    <span>Outstanding</span>
                    <strong>{fmt(selectedDocument.outstanding)}</strong>
                  </div>
                )}
            </div>
            <p className="document-terms">
              {selectedDocument.terms || selectedDocument.reason}
            </p>
            <div className="document-actions">
              {selectedDocument.kind === "quote" &&
                ["approved", "sent", "declined"].includes(
                  selectedDocument.status,
                ) &&
                canSell && (
                  <button
                    type="button"
                    className="button secondary"
                    onClick={() =>
                      setDialog({
                        type: "document",
                        kind: "quote",
                        document: selectedDocument,
                      })
                    }
                  >
                    Create revision
                  </button>
                )}
              {["draft", "rejected"].includes(selectedDocument.status) &&
                (selectedDocument.kind === "quote" ? canSell : canFinance) && (
                  <>
                    <button
                      className="button secondary"
                      onClick={() =>
                        setDialog({
                          type: "document",
                          kind: selectedDocument.kind,
                          document: selectedDocument,
                        })
                      }
                    >
                      Revise
                    </button>
                    <button
                      className="button"
                      disabled={busy}
                      onClick={() =>
                        void documentAction(selectedDocument, "submit")
                      }
                    >
                      Submit for approval
                    </button>
                  </>
                )}
              {selectedDocument.kind === "quote" &&
                selectedDocument.status === "approved" &&
                canSell && (
                  <button
                    className="button"
                    disabled={busy}
                    onClick={() =>
                      void documentAction(selectedDocument, "send")
                    }
                  >
                    Mark as sent
                  </button>
                )}
              {selectedDocument.kind === "quote" &&
                selectedDocument.status === "sent" &&
                canSell && (
                  <>
                    <button
                      className="button"
                      disabled={busy}
                      onClick={() =>
                        void documentAction(selectedDocument, "accept")
                      }
                    >
                      Record acceptance
                    </button>
                    <button
                      className="button secondary"
                      disabled={busy}
                      onClick={() =>
                        void documentAction(selectedDocument, "decline")
                      }
                    >
                      Record decline
                    </button>
                  </>
                )}
              {selectedDocument.kind === "quote" &&
                selectedDocument.status === "accepted" &&
                canFinance && (
                  <button
                    className="button"
                    disabled={
                      busy ||
                      store.documents.some(
                        (value) => value.quote_id === selectedDocument.id,
                      )
                    }
                    onClick={() =>
                      void documentAction(selectedDocument, "convert")
                    }
                  >
                    Create invoice
                  </button>
                )}
              {selectedDocument.kind === "invoice" &&
                selectedDocument.status === "issued" &&
                Number(selectedDocument.outstanding) > 0 &&
                canFinance && (
                  <button
                    className="button"
                    onClick={() => setDialog({ type: "payment" })}
                  >
                    Record payment
                  </button>
                )}
              {["draft", "pending", "rejected", "approved", "sent"].includes(
                selectedDocument.status,
              ) &&
                (selectedDocument.kind === "quote" ? canSell : canFinance) && (
                  <button
                    className="text-button danger"
                    disabled={busy}
                    onClick={() =>
                      void documentAction(selectedDocument, "cancel")
                    }
                  >
                    Cancel document
                  </button>
                )}
            </div>
            <RevisionHistory
              identifier={selectedDocument.id}
              revision={selectedDocument.revision}
              fmt={fmt}
            />
            {selectedDocument.kind === "quote" && (
              <div className="info-note">
                Sent, acceptance and decline actions record your customer
                communication. Messages are sent outside the CRM in this
                release.
              </div>
            )}
          </div>
        </Modal>
      )}
      {dialog?.type === "viewCustomer" && (
        <CustomerDetail
          customer={
            store.customers.find((value) => value.id === dialog.customer.id) ??
            dialog.customer
          }
          close={close}
          edit={
            canSell
              ? () => setDialog({ type: "customer", customer: dialog.customer })
              : undefined
          }
          store={store}
          fmt={fmt}
          canUpload={canSell}
        />
      )}
    </div>
  );
}

function RevisionHistory({
  identifier,
  revision,
  fmt,
}: {
  identifier: number;
  revision: number;
  fmt: (value: string | number) => string;
}) {
  const [values, setValues] = useState<SalesDocument[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    api<SalesDocument[]>(`/documents/${identifier}/revisions`)
      .then((value) => {
        if (live) setValues(value);
      })
      .catch((error) => {
        if (live) setError(errorMessage(error));
      });
    return () => {
      live = false;
    };
  }, [identifier, revision]);
  return (
    <>
      {error && <div className="error">{error}</div>}
      {values.length > 1 && (
        <div className="revision-history">
          <h3>Revision history</h3>
          {values.slice(0, -1).map((value) => (
            <details key={value.revision}>
              <summary>
                Revision {value.revision} · {fmt(value.total)} · {value.status}
              </summary>
              <p>
                {value.items
                  .map(
                    (item) =>
                      `${item.description}: ${item.quantity} × ${fmt(item.unit_price)}`,
                  )
                  .join(" · ")}
              </p>
              <p>
                Discount {value.discount_rate}% · Tax {value.tax_rate}%
              </p>
              <p>{value.terms || value.reason}</p>
            </details>
          ))}
        </div>
      )}
    </>
  );
}

function Login({ onLogin }: { onLogin: (user: User) => Promise<void> }) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [invite, setInvite] = useState(
    () => new URLSearchParams(window.location.search).get("invite") ?? "",
  );
  const [notice, setNotice] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      if (invite) {
        if (text(form, "password") !== text(form, "confirm"))
          throw new Error("The passwords do not match.");
        await api("/auth/accept-invite", "POST", {
          token: invite,
          password: text(form, "password"),
        });
        window.history.replaceState(null, "", window.location.pathname);
        setInvite("");
        setNotice("Password set. Sign in with your email and new password.");
        return;
      }
      const user = await api<User>("/auth/login", "POST", {
        email: text(form, "email"),
        password: text(form, "password"),
      });
      await onLogin(user);
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="login-screen">
      <div className="login-art">
        <div className="brand">
          <div className="brand-symbol">
            n<span>.</span>
          </div>
          <div>
            <strong>nexera</strong>
            <small>BUSINESS CRM</small>
          </div>
        </div>
        <div className="login-copy">
          <span className="eyebrow">YOUR BUSINESS, CONNECTED</span>
          <h1>
            Good relationships.
            <br />
            Great possibilities.
          </h1>
          <p>
            A calmer way to manage your customers, grow your sales and stay on
            top of every account.
          </p>
          <div className="login-features">
            <span>
              <Users size={18} />
              Customers
            </span>
            <span>
              <TrendingUp size={18} />
              Sales
            </span>
            <span>
              <BookOpen size={18} />
              Accounts
            </span>
          </div>
        </div>
        <div className="login-orbit">
          <div />
          <div />
          <div />
        </div>
        <small className="login-footer">
          One workspace. Your whole customer journey.
        </small>
      </div>
      <div className="login-form-wrap">
        <form className="login-form" onSubmit={submit}>
          <div className="login-icon">
            <Building2 size={24} />
          </div>
          <h2>{invite ? "Set your password" : "Welcome to your workspace"}</h2>
          <p>
            {invite
              ? "Choose a password of at least 12 characters to activate your account."
              : "Sign in to pick up where you left off."}
          </p>
          {notice && (
            <div className="info-strip" role="status">
              {notice}
            </div>
          )}
          {!invite && (
            <Field label="Email address">
              <input
                name="email"
                type="email"
                required
                autoComplete="username"
                placeholder="you@company.com"
              />
            </Field>
          )}
          <Field label={invite ? "New password" : "Password"}>
            <input
              name="password"
              type="password"
              required
              minLength={invite ? 12 : undefined}
              autoComplete={invite ? "new-password" : "current-password"}
              placeholder={
                invite ? "At least 12 characters" : "Enter your password"
              }
            />
          </Field>
          {invite && (
            <Field label="Confirm password">
              <input
                name="confirm"
                type="password"
                required
                minLength={12}
                autoComplete="new-password"
              />
            </Field>
          )}
          {error && (
            <div className="error" role="alert">
              {error}
            </div>
          )}
          <button className="button login-submit" disabled={busy}>
            {busy ? (
              <Loader2 className="spin" size={17} />
            ) : (
              <ArrowRight size={17} />
            )}
            {busy ? "Please wait…" : invite ? "Set password" : "Sign in"}
          </button>
          <div className="login-note">
            <ShieldCheck size={15} />
            Your workspace is protected with role-based access.
          </div>
        </form>
      </div>
    </div>
  );
}

function DocumentTable({
  values,
  fmt,
  open,
  invoices = false,
}: {
  values: SalesDocument[];
  fmt: (value: string | number) => string;
  open: (document: SalesDocument) => void;
  invoices?: boolean;
}) {
  if (!values.length)
    return (
      <Empty
        title="No documents found"
        detail="Create a new document or adjust your search."
      />
    );
  return (
    <Table
      headers={[
        "Document",
        "Customer",
        invoices ? "Due date" : "Date",
        "Amount",
        "Status",
        "",
      ]}
    >
      {values.map((value) => (
        <tr key={value.id}>
          <td>
            <button className="document-link" onClick={() => open(value)}>
              <span className="file-icon">
                <FileText size={17} />
              </span>
              <strong>{value.number}</strong>
            </button>
            <small className="document-sub">Revision {value.revision}</small>
          </td>
          <td>{value.customer}</td>
          <td>{displayDate(invoices ? value.due : value.date)}</td>
          <td className="amount">
            {fmt(value.total)}
            {invoices && value.status === "issued" && (
              <small>{fmt(value.outstanding)} due</small>
            )}
          </td>
          <td>
            <Badge
              tone={tone(
                invoices && value.status === "issued"
                  ? value.payment_state
                  : value.status,
              )}
            >
              {invoices && value.status === "issued"
                ? value.payment_state
                : value.status}
            </Badge>
            <AccountingBadge info={value.accounting} />
          </td>
          <td>
            <button
              className="icon-button"
              aria-label={`Open ${value.number}`}
              onClick={() => open(value)}
            >
              <ChevronRight size={17} />
            </button>
          </td>
        </tr>
      ))}
    </Table>
  );
}

function RevenueChart({
  report,
  fmt,
}: {
  report: Report;
  fmt: (value: string | number) => string;
}) {
  const maximum = Math.max(
    ...report.trend.flatMap((value) => [
      Math.abs(Number(value.invoiced)),
      Math.abs(Number(value.collected)),
    ]),
    1,
  );
  return (
    <>
      <div className="revenue-summary">
        <div>
          <span>Net invoiced sales</span>
          <strong>{fmt(report.net_sales)}</strong>
        </div>
        <div>
          <span>Collections</span>
          <strong>{fmt(report.collected)}</strong>
        </div>
        <div className="chart-key">
          <span>
            <i />
            Invoiced
          </span>
          <span>
            <i />
            Collected
          </span>
        </div>
      </div>
      {report.trend.length ? (
        <div className="chart">
          <div className="chart-guides">
            <span>{fmt(maximum)}</span>
            <span>{fmt(maximum / 2)}</span>
            <span>0</span>
          </div>
          <div className="chart-bars">
            {report.trend.map((value) => (
              <div className="chart-month" key={value.month}>
                <div className="bar-pair">
                  <div
                    className={`bar invoiced ${Number(value.invoiced) < 0 ? "negative" : ""}`}
                    title={`Net invoiced: ${fmt(value.invoiced)}`}
                    style={{
                      height: `${Math.max(2, (Math.abs(Number(value.invoiced)) / maximum) * 100)}%`,
                    }}
                  />
                  <div
                    className="bar collected"
                    title={`Collected: ${fmt(value.collected)}`}
                    style={{
                      height: `${Math.max(2, (Number(value.collected) / maximum) * 100)}%`,
                    }}
                  />
                </div>
                <span>
                  {new Date(`${value.month}-01T12:00:00`).toLocaleDateString(
                    "en-US",
                    { month: "short", year: "2-digit" },
                  )}
                </span>
              </div>
            ))}
          </div>
        </div>
      ) : (
        <Empty
          title="Your revenue story starts here"
          detail="Issued invoices and payments will appear in this chart."
        />
      )}
    </>
  );
}

function ApprovalDialog({
  approval,
  close,
  refresh,
  fmt,
}: {
  approval: Approval;
  close: () => void;
  refresh: () => Promise<void>;
  fmt: (value: string | number) => string;
}) {
  const [decision, setDecision] = useState("approved");
  return (
    <Modal
      title={`Review ${approval.document.number}`}
      subtitle={`${approval.requester} · ${approval.document.customer}`}
      close={close}
      submitLabel="Confirm decision"
      save={async (form) => {
        await api(`/approvals/${approval.id}/decision`, "POST", {
          decision,
          comment: text(form, "comment"),
        });
        await refresh();
      }}
    >
      <div className="review-amount">
        <span>{approval.document.kind} total</span>
        <strong>{fmt(approval.document.total)}</strong>
      </div>
      <p>{approval.document.reason || approval.document.terms}</p>
      {approval.document.items.map((item, index) => (
        <div className="review-item" key={index}>
          <span>{item.description}</span>
          <strong>
            {item.quantity} × {fmt(item.unit_price)}
          </strong>
        </div>
      ))}
      <Field label="Decision">
        <select
          value={decision}
          onChange={(event) => setDecision(event.target.value)}
        >
          <option value="approved">Approve</option>
          <option value="rejected">Reject and return for revision</option>
        </select>
      </Field>
      <Field label="Review comment">
        <textarea
          name="comment"
          rows={3}
          required={decision === "rejected"}
          placeholder="Explain your decision…"
        />
      </Field>
      <div className="info-note">
        Approving an invoice or account transaction posts it to the customer
        ledger.
      </div>
    </Modal>
  );
}

function CustomerDetail({
  customer,
  close,
  edit,
  store,
  fmt,
  canUpload,
}: {
  customer: Customer;
  close: () => void;
  edit?: () => void;
  store: Store;
  fmt: (value: string | number) => string;
  canUpload: boolean;
}) {
  const [ledger, setLedger] = useState<Ledger | null>(null);
  const [activities, setActivities] = useState<Activity[]>([]);
  const [attachments, setAttachments] = useState<
    { id: number; name: string; size: number }[]
  >([]);
  const [error, setError] = useState("");
  const [uploading, setUploading] = useState(false);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [tab, setTab] = useState("Overview");
  useEffect(() => {
    let live = true;
    setError("");
    api<Ledger>(
      `/customers/${customer.id}/ledger?${new URLSearchParams({ ...(start ? { start } : {}), ...(end ? { end } : {}) })}`,
    )
      .then((value) => {
        if (live) setLedger(value);
      })
      .catch((error) => {
        if (live) setError(errorMessage(error));
      });
    return () => {
      live = false;
    };
  }, [customer.id, start, end]);
  useEffect(() => {
    let live = true;
    api<{
      activities: Activity[];
      attachments: { id: number; name: string; size: number }[];
    }>(`/customers/${customer.id}/history`)
      .then((value) => {
        if (live) {
          setActivities(value.activities);
          setAttachments(value.attachments);
        }
      })
      .catch((error) => {
        if (live) setError(errorMessage(error));
      });
    return () => {
      live = false;
    };
  }, [customer.id]);
  const query = new URLSearchParams({
    ...(start ? { start } : {}),
    ...(end ? { end } : {}),
  }).toString();
  return (
    <Modal
      title={customer.name}
      subtitle={customer.contact || customer.kind}
      close={close}
      wide
    >
      <div className="customer-detail-heading">
        <span className="customer-avatar large color-0">
          {initials(customer.name)}
        </span>
        <div>
          <span>Current account balance</span>
          <strong>{fmt(customer.balance)}</strong>
        </div>
        {edit && (
          <button
            type="button"
            className="button secondary small"
            onClick={edit}
          >
            Edit customer
          </button>
        )}
      </div>
      <div className="tabs">
        {["Overview", "Ledger", "Activity", "Attachments"].map((value) => (
          <button
            type="button"
            key={value}
            className={tab === value ? "active" : ""}
            onClick={() => setTab(value)}
          >
            {value}
          </button>
        ))}
      </div>
      {error && <div className="error">{error}</div>}
      {tab === "Overview" && (
        <>
          <div className="detail-grid">
            {[
              ["Email", customer.email],
              ["Phone", customer.phone],
              ["Address", customer.address],
              ["Tax ID", customer.tax_id],
              ["Payment terms", `${customer.payment_days} days`],
              ["Tags", customer.tags],
            ].map(([label, value]) => (
              <div key={label}>
                <small>{label}</small>
                <strong>{value || "—"}</strong>
              </div>
            ))}
          </div>
          <h3>Notes</h3>
          <p className="pre-wrap">{customer.notes || "No notes recorded."}</p>
          {customer.contacts.length > 0 && (
            <>
              <h3>Additional contacts</h3>
              {customer.contacts.map((value, index) => (
                <p key={index}>
                  {value.name} · {value.email} · {value.phone}
                </p>
              ))}
            </>
          )}
          {customer.addresses.length > 0 && (
            <>
              <h3>Additional addresses</h3>
              {customer.addresses.map((value, index) => (
                <p className="pre-wrap" key={index}>
                  <strong>{value.label}</strong>
                  <br />
                  {value.address}
                </p>
              ))}
            </>
          )}
          <h3>Documents</h3>
          {store.documents
            .filter((value) => value.customer_id === customer.id)
            .map((value) => (
              <div className="allocation-row" key={value.id}>
                <span>
                  {value.number} · {value.kind}
                </span>
                <Badge tone={tone(value.status)}>{value.status}</Badge>
                <strong>{fmt(value.total)}</strong>
              </div>
            ))}
        </>
      )}
      {tab === "Ledger" && (
        <>
          <div className="ledger-toolbar">
            <Field label="From">
              <input
                type="date"
                value={start}
                onChange={(event) => setStart(event.target.value)}
              />
            </Field>
            <Field label="To">
              <input
                type="date"
                value={end}
                onChange={(event) => setEnd(event.target.value)}
              />
            </Field>
            <a
              className="button secondary small"
              href={`/api/customers/${customer.id}/statement?${query}`}
            >
              <Download size={15} />
              PDF
            </a>
            <a
              className="button secondary small"
              href={`/api/customers/${customer.id}/statement?format=csv&${query}`}
            >
              CSV
            </a>
          </div>
          {ledger ? (
            <>
              <div className="ledger-summary">
                <span>
                  Opening <strong>{fmt(ledger.opening)}</strong>
                </span>
                <span>
                  Closing <strong>{fmt(ledger.balance)}</strong>
                </span>
              </div>
              <Table
                headers={["Date", "Transaction", "Debit", "Credit", "Balance"]}
              >
                {ledger.entries.map((value) => (
                  <tr key={value.id}>
                    <td>{displayDate(value.date)}</td>
                    <td>{value.description}</td>
                    <td>{Number(value.debit) ? fmt(value.debit) : "—"}</td>
                    <td>{Number(value.credit) ? fmt(value.credit) : "—"}</td>
                    <td className="amount">{fmt(value.balance)}</td>
                  </tr>
                ))}
              </Table>
              {!ledger.entries.length && (
                <Empty title="No ledger entries in this period" />
              )}
            </>
          ) : (
            <Loader2 className="spin" />
          )}
        </>
      )}
      {tab === "Activity" && (
        <div className="activity-list">
          {activities.length ? (
            activities.map((value) => (
              <div className="activity-item" key={value.id}>
                <span className="activity-dot" />
                <div>
                  <strong>{value.action}</strong>
                  <p>{value.detail}</p>
                  <small>{displayDate(value.created)}</small>
                </div>
              </div>
            ))
          ) : (
            <Empty title="No activity recorded" />
          )}
        </div>
      )}
      {tab === "Attachments" && (
        <>
          <p className="muted">
            PDF, images, CSV, text, Word and Excel files · up to 10 MB per file.
          </p>
          {canUpload && (
            <label className="button secondary small">
              <Upload size={16} />
              {uploading ? "Uploading…" : "Upload attachment"}
              <input
                disabled={uploading}
                hidden
                type="file"
                accept=".pdf,.png,.jpg,.jpeg,.csv,.txt,.docx,.xlsx"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (!file) return;
                  const form = new FormData();
                  form.set("file", file);
                  setUploading(true);
                  void api<{ id: number; name: string; size: number }>(
                    `/customers/${customer.id}/attachments`,
                    "POST",
                    form,
                  )
                    .then((value) => setAttachments([...attachments, value]))
                    .catch((error) => setError(errorMessage(error)))
                    .finally(() => setUploading(false));
                  event.target.value = "";
                }}
              />
            </label>
          )}
          {attachments.map((value) => (
            <a
              className="attachment-row"
              href={`/api/attachments/${value.id}`}
              key={value.id}
            >
              <FileText size={18} />
              <strong>{value.name}</strong>
              <span>{Math.ceil(value.size / 1024)} KB</span>
              <Download size={16} />
            </a>
          ))}
        </>
      )}
    </Modal>
  );
}

const emptySync: AccountingData["sync"] = {
  active: false,
  enabled: false,
  cutoff_ledger_id: 0,
  historical_count: 0,
  customer_balance: "0",
  pending: 0,
  postings: [],
};

type HostedStatus = {
  mode: "hosted" | "single";
  enabled: boolean;
  pausedReason: string | null;
  erp: { companyName: string; currency: string };
  counts: Record<string, number>;
  lastPostedAt: string | null;
};

type OutboxRow = {
  id: number;
  type: string;
  reference: string | null;
  date: string | null;
  total: string | null;
  status: string;
  attempts: number;
  nextAttemptAt: string | null;
  lastErrorCode: string | null;
  lastErrorMessage: string | null;
  erpJournalNumber: number | null;
};

const accountingLabels: Record<string, string> = {
  pending: "Posting queued",
  in_progress: "Posting",
  retrying: "Retrying",
  blocked: "Posting blocked",
  held: "Posting paused",
  conflict: "Needs support",
  posted: "Posted",
  skipped: "Manual posting",
};

function accountingTone(status: string) {
  if (status === "posted") return "green";
  if (["blocked", "conflict"].includes(status)) return "red";
  if (["held", "retrying", "skipped"].includes(status)) return "amber";
  return "";
}

function AccountingBadge({ info }: { info?: AccountingInfo | null }) {
  if (!info) return null;
  return (
    <span title={info.lastErrorMessage ?? undefined}>
      <Badge tone={accountingTone(info.status)}>
        {info.status === "posted" && info.erpJournalNumber
          ? `Journal #${info.erpJournalNumber}`
          : (accountingLabels[info.status] ?? info.status)}
      </Badge>
    </span>
  );
}

const pauseMessages: Record<string, string> = {
  ERP_POSTING_DISABLED:
    "Automatic posting is switched off in the ERP. Entries stay queued and will post once it is enabled.",
  CREDENTIAL_REJECTED:
    "The ERP rejected this workspace's connection. Your provider has been notified; entries stay queued.",
};

function PostingQueue({
  status,
  canRetry,
  fmt,
}: {
  status: HostedStatus;
  canRetry: boolean;
  fmt: (value: string | number) => string;
}) {
  const [rows, setRows] = useState<OutboxRow[]>([]);
  const [filter, setFilter] = useState("");
  const [version, setVersion] = useState(0);
  const [message, setMessage] = useState("");
  const [current, setCurrent] = useState(status);
  useEffect(() => {
    let live = true;
    Promise.all([
      api<OutboxRow[]>(
        `/accounting/outbox?limit=50${filter ? `&status=${filter}` : ""}`,
      ),
      api<HostedStatus>("/accounting/status"),
    ])
      .then(([values, latest]) => {
        if (!live) return;
        setRows(values);
        setCurrent(latest);
      })
      .catch((reason) => {
        if (live) setMessage(errorMessage(reason));
      });
    return () => {
      live = false;
    };
  }, [filter, version]);
  return (
    <>
      {current.pausedReason && (
        <div className="panel report-error" role="alert">
          {pauseMessages[current.pausedReason] ??
            `Automatic posting is paused (${current.pausedReason}).`}
        </div>
      )}
      <section className="panel">
        <div className="panel-heading">
          <h2>Posting queue</h2>
          <Badge tone={current.enabled ? "green" : ""}>
            {current.enabled
              ? `Automatic posting to ${current.erp.companyName || "ERP"}`
              : "Automatic posting not enabled"}
          </Badge>
        </div>
        <p>
          Issued invoices, credit notes, refunds and recorded payments post to
          the ERP automatically. Opening balances and adjustments need manual
          posting by your accountant.
          {current.lastPostedAt &&
            ` Last posted ${displayDate(current.lastPostedAt.slice(0, 10))}.`}
        </p>
        <div className="report-toolbar">
          {Object.entries(current.counts).map(([key, count]) => (
            <Badge key={key}>
              {key}: {count}
            </Badge>
          ))}
          <Field label="Status">
            <select
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
            >
              <option value="">All</option>
              {Object.keys(accountingLabels).map((key) => (
                <option key={key} value={key}>
                  {accountingLabels[key]}
                </option>
              ))}
            </select>
          </Field>
          <button
            className="button secondary"
            onClick={() => setVersion((value) => value + 1)}
          >
            Refresh queue
          </button>
        </div>
        {message && (
          <div className="info-strip" role="status">
            {message}
          </div>
        )}
        <Table
          headers={[
            "Reference",
            "Type",
            "Date",
            "Amount",
            "Status",
            "Detail",
            "",
          ]}
        >
          {rows.map((row) => (
            <tr key={row.id}>
              <td>{row.reference}</td>
              <td>{row.type}</td>
              <td>{row.date ? displayDate(row.date) : ""}</td>
              <td className="amount">{row.total ? fmt(row.total) : ""}</td>
              <td>
                <Badge tone={accountingTone(row.status)}>
                  {accountingLabels[row.status] ?? row.status}
                </Badge>
              </td>
              <td>
                {row.erpJournalNumber
                  ? `Journal #${row.erpJournalNumber}`
                  : (row.lastErrorMessage ?? row.lastErrorCode ?? "")}
              </td>
              <td>
                {canRetry &&
                  ["blocked", "retrying", "held"].includes(row.status) && (
                    <button
                      className="text-button"
                      onClick={() => {
                        void api(`/accounting/outbox/${row.id}/retry`, "POST")
                          .then(() => {
                            setMessage("Queued for another attempt.");
                            setVersion((value) => value + 1);
                          })
                          .catch((reason) => setMessage(errorMessage(reason)));
                      }}
                    >
                      Retry now
                    </button>
                  )}
              </td>
            </tr>
          ))}
        </Table>
      </section>
    </>
  );
}

type AccountingData = {
  connection: {
    tenantId: string;
    companyId: string;
    tenantName: string;
    companyName: string;
    currency: string;
  };
  accounts: {
    id: string;
    code: string;
    name: { en: string };
    type: string;
    isPostable: boolean;
    isActive: boolean;
  }[];
  sync: {
    active: boolean;
    enabled: boolean;
    cutoff_ledger_id: number;
    historical_count: number;
    customer_balance: string;
    pending: number;
    postings: { ledger_id: number; journal_id: string; number: number }[];
  };
  trial: {
    rows: {
      accountId: string;
      code: string;
      name: { en: string };
      closingDebit: string;
      closingCredit: string;
    }[];
    totals: { closingDebit: string; closingCredit: string };
  };
  journals: {
    items: {
      id: string;
      number: number;
      date: string;
      description: string;
      totalDebit: string;
    }[];
  };
};

function AccountingPage({
  fmt,
  canActivate,
  canRetry,
}: {
  fmt: (value: string | number) => string;
  canActivate: boolean;
  canRetry: boolean;
}) {
  const [hostedStatus, setHostedStatus] = useState<HostedStatus | null>(null);
  useEffect(() => {
    let live = true;
    api<HostedStatus>("/accounting/status")
      .then((status) => {
        if (live) setHostedStatus(status);
      })
      .catch(() => {
        if (live) setHostedStatus(null);
      });
    return () => {
      live = false;
    };
  }, []);
  const hostedMode = hostedStatus?.mode === "hosted";
  const [data, setData] = useState<AccountingData | null>(null);
  const [error, setError] = useState("");
  const [end, setEnd] = useState(today());
  const [start, setStart] = useState(`${today().slice(0, 4)}-01-01`);
  const [version, setVersion] = useState(0);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  useEffect(() => {
    let live = true;
    setError("");
    setData(null);
    api<AccountingData["connection"]>("/accounting/connection")
      .then(async (connection) => {
        const [accounts, trial, journals, sync] = await Promise.all([
          api<AccountingData["accounts"]>("/accounting/accounts"),
          api<AccountingData["trial"]>(
            `/accounting/trial-balance?start=${start}&end=${end}`,
          ),
          api<AccountingData["journals"]>("/accounting/journals"),
          api<AccountingData["sync"]>("/accounting/sync-status").catch(
            () => emptySync,
          ),
        ]);
        if (live) setData({ connection, accounts, trial, journals, sync });
      })
      .catch((reason) => {
        if (live) setError(errorMessage(reason));
      });
    return () => {
      live = false;
    };
  }, [start, end, version]);
  const mappings = [
    { key: "receivable", label: "Customer receivables", types: ["ASSET"] },
    { key: "revenue", label: "Invoice revenue", types: ["REVENUE"] },
    { key: "receipts", label: "Receipt / refund clearing", types: ["ASSET"] },
    { key: "returns", label: "Sales returns", types: ["REVENUE"] },
    { key: "opening", label: "Opening balance equity", types: ["EQUITY"] },
    {
      key: "adjustments",
      label: "Customer adjustments",
      types: ["EQUITY", "REVENUE", "EXPENSE"],
    },
    {
      key: "tax",
      label: "Tax payable (optional for untaxed businesses)",
      types: ["LIABILITY"],
    },
  ];
  return (
    <>
      {hostedMode && hostedStatus && (
        <PostingQueue status={hostedStatus} canRetry={canRetry} fmt={fmt} />
      )}
      <div className="report-toolbar">
        <Field label="From">
          <input
            type="date"
            value={start}
            onChange={(event) => setStart(event.target.value)}
          />
        </Field>
        <Field label="To">
          <input
            type="date"
            value={end}
            onChange={(event) => setEnd(event.target.value)}
          />
        </Field>
        <button
          className="button secondary"
          onClick={() => setVersion((value) => value + 1)}
        >
          Refresh accounting
        </button>
      </div>
      {message && (
        <div className="info-strip" role="status">
          {message}
        </div>
      )}
      {error && (
        <div className="panel report-error" role="alert">
          {error}
        </div>
      )}
      {!error && !data && <div className="panel">Loading accounting data…</div>}
      {data && (
        <>
          <section className="panel">
            <div className="panel-heading">
              <h2>{data.connection.companyName}</h2>
              <Badge tone="green">
                Connected to {data.connection.tenantName}
              </Badge>
            </div>
            <p>
              General ledger from Nexera ERP · {data.connection.currency}.
              Customer accounts remain in CRM.
            </p>
          </section>
          {!hostedMode && (
            <section className="panel space-top">
              <div className="panel-heading">
                <h2>Company reconciliation & synchronization</h2>
                <Badge>
                  {data.sync.active
                    ? `${data.sync.pending} pending`
                    : "Not activated"}
                </Badge>
              </div>
              <p>
                CRM customer balance: {fmt(data.sync.customer_balance)}.{" "}
                {data.sync.historical_count} existing transactions are excluded
                from automatic posting and must be reconciled with ERP first.
              </p>
              {!data.sync.active && canActivate && (
                <form
                  className="form-grid"
                  onSubmit={(event) => {
                    event.preventDefault();
                    const form = new FormData(event.currentTarget);
                    const account_ids = Object.fromEntries(
                      mappings
                        .filter((mapping) => form.get(mapping.key))
                        .map((mapping) => [
                          mapping.key,
                          String(form.get(mapping.key)),
                        ]),
                    );
                    setBusy(true);
                    void api("/accounting/activate", "POST", {
                      tenant_id: data.connection.tenantId,
                      company_id: data.connection.companyId,
                      cutoff_ledger_id: data.sync.cutoff_ledger_id,
                      reconciled: true,
                      account_ids,
                    })
                      .then(() => {
                        setMessage(
                          "Company mapping saved. Only new transactions after this cutoff are eligible for posting.",
                        );
                        setVersion((value) => value + 1);
                      })
                      .catch((reason) => setMessage(errorMessage(reason)))
                      .finally(() => setBusy(false));
                  }}
                >
                  {mappings.map((mapping) => (
                    <Field key={mapping.key} label={mapping.label}>
                      <select
                        name={mapping.key}
                        required={mapping.key !== "tax"}
                        defaultValue=""
                      >
                        <option value="">Select an account</option>
                        {data.accounts
                          .filter(
                            (account) =>
                              account.isActive &&
                              account.isPostable &&
                              mapping.types.includes(account.type),
                          )
                          .map((account) => (
                            <option key={account.id} value={account.id}>
                              {account.code} · {account.name.en}
                            </option>
                          ))}
                      </select>
                    </Field>
                  ))}
                  <label>
                    <input type="checkbox" required /> I have reconciled
                    existing customer balances in ERP and approve this
                    future-only cutoff. These account mappings cannot be changed
                    after activation.
                  </label>
                  <button className="button" disabled={busy}>
                    Confirm company mapping
                  </button>
                </form>
              )}
              {data.sync.active && (
                <button
                  className="button secondary"
                  disabled={busy || !data.sync.enabled || !data.sync.pending}
                  onClick={() => {
                    setBusy(true);
                    void api<{ posted: number; failure: string | null }>(
                      "/accounting/sync",
                      "POST",
                      {},
                    )
                      .then((result) => {
                        setMessage(
                          `${result.posted} journals linked.${result.failure ? ` ${result.failure}` : ""}`,
                        );
                        setVersion((value) => value + 1);
                      })
                      .catch((reason) => setMessage(errorMessage(reason)))
                      .finally(() => setBusy(false));
                  }}
                >
                  Synchronize up to 10 transactions
                </button>
              )}
              {!data.sync.enabled && (
                <p>
                  Posting is disabled until an administrator enables
                  synchronization after reconciliation. Reading accounting data
                  does not post transactions.
                </p>
              )}
              {data.sync.postings.length > 0 && (
                <Table
                  headers={["CRM ledger entry", "ERP journal", "Journal ID"]}
                >
                  {data.sync.postings.map((posting) => (
                    <tr key={posting.ledger_id}>
                      <td>{posting.ledger_id}</td>
                      <td>{posting.number}</td>
                      <td>{posting.journal_id}</td>
                    </tr>
                  ))}
                </Table>
              )}
            </section>
          )}
          <section className="panel space-top">
            <div className="panel-heading">
              <h2>Trial balance</h2>
            </div>
            <Table headers={["Account", "Debit", "Credit"]}>
              {data.trial.rows.map((row) => (
                <tr key={row.accountId}>
                  <td>
                    {row.code} · {row.name.en}
                  </td>
                  <td className="amount">{fmt(row.closingDebit)}</td>
                  <td className="amount">{fmt(row.closingCredit)}</td>
                </tr>
              ))}
              <tr>
                <td>
                  <strong>Total</strong>
                </td>
                <td className="amount">
                  <strong>{fmt(data.trial.totals.closingDebit)}</strong>
                </td>
                <td className="amount">
                  <strong>{fmt(data.trial.totals.closingCredit)}</strong>
                </td>
              </tr>
            </Table>
          </section>
          <section className="panel space-top">
            <div className="panel-heading">
              <h2>Chart of accounts</h2>
              <Badge>{data.accounts.length} accounts</Badge>
            </div>
            <Table headers={["Code", "Account", "Type"]}>
              {data.accounts.map((account) => (
                <tr key={account.id}>
                  <td>{account.code}</td>
                  <td>{account.name.en}</td>
                  <td>{account.type}</td>
                </tr>
              ))}
            </Table>
          </section>
          <section className="panel space-top">
            <div className="panel-heading">
              <h2>Recent journals</h2>
            </div>
            <Table headers={["Number", "Date", "Description", "Amount"]}>
              {data.journals.items.map((journal) => (
                <tr key={journal.id}>
                  <td>{journal.number}</td>
                  <td>{displayDate(journal.date)}</td>
                  <td>{journal.description}</td>
                  <td className="amount">{fmt(journal.totalDebit)}</td>
                </tr>
              ))}
            </Table>
          </section>
        </>
      )}
    </>
  );
}

function ReportsPage({
  fmt,
  onError,
}: {
  fmt: (value: string | number) => string;
  onError: (message: string) => void;
}) {
  const [report, setReport] = useState<Report | null>(null);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [error, setError] = useState("");
  const query = new URLSearchParams({
    ...(start ? { start } : {}),
    ...(end ? { end } : {}),
  }).toString();
  useEffect(() => {
    let live = true;
    setError("");
    api<Report>(`/reports?${query}`)
      .then((value) => {
        if (live) setReport(value);
      })
      .catch((error) => {
        if (live) setError(errorMessage(error));
      });
    return () => {
      live = false;
    };
  }, [query]);
  if (error)
    return (
      <div className="panel report-error">
        <div className="error">{error}</div>
        <button
          className="button secondary"
          onClick={() => {
            setStart("");
            setEnd("");
            onError(error);
          }}
        >
          Reset date filters
        </button>
      </div>
    );
  return (
    <>
      <div className="report-toolbar">
        <div>
          <Field label="Sales / collection period from">
            <input
              type="date"
              value={start}
              onChange={(event) => setStart(event.target.value)}
            />
          </Field>
          <Field label="To">
            <input
              type="date"
              value={end}
              onChange={(event) => setEnd(event.target.value)}
            />
          </Field>
        </div>
        <a className="button secondary" href={`/api/exports/reports?${query}`}>
          <Download size={16} />
          Export report
        </a>
      </div>
      {report ? (
        <>
          <div className="stats-grid">
            <Stat
              label="Net invoiced sales"
              value={fmt(report.net_sales)}
              hint={`${fmt(report.credited)} in credit notes`}
              icon={<FileText size={18} />}
            />
            <Stat
              label="Customer collections"
              value={fmt(report.collected)}
              hint={`${fmt(report.refunded)} refunded`}
              icon={<ArrowDownLeft size={18} />}
            />
            <Stat
              label="Outstanding invoices"
              value={fmt(report.outstanding)}
              hint={`Current balance as of ${displayDate(report.receivables_as_of)}`}
              icon={<BookOpen size={18} />}
            />
            <Stat
              label="Quote conversion"
              value={`${report.quote_conversion}%`}
              hint="All-time quotes converted into invoice drafts"
              icon={<TrendingUp size={18} />}
            />
          </div>
          <section className="panel">
            <div className="panel-heading">
              <div>
                <h2>Receivables ageing</h2>
                <p>
                  Current invoice balances · opening balances and account
                  adjustments appear in customer ledgers
                </p>
              </div>
            </div>
            <div className="ageing-grid">
              {[
                "Not overdue",
                "1–30 days",
                "31–60 days",
                "61–90 days",
                "90+ days",
              ].map((label, index) => (
                <div key={label}>
                  <small>{label}</small>
                  <strong>{fmt(report.ageing[index])}</strong>
                  <span className={`ageing-bar ageing-${index}`} />
                </div>
              ))}
            </div>
          </section>
          <div className="report-grid">
            <section className="panel">
              <div className="panel-heading">
                <h2>Sales performance</h2>
                <span className="muted small-text">
                  All-time opportunity results
                </span>
              </div>
              <Table headers={["Salesperson", "Deals", "Won", "Won value"]}>
                {report.salespeople.map((value) => (
                  <tr key={value.name}>
                    <td>{value.name}</td>
                    <td>{value.deals}</td>
                    <td>{value.won}</td>
                    <td className="amount">{fmt(value.won_value)}</td>
                  </tr>
                ))}
              </Table>
            </section>
            <section className="panel">
              <div className="panel-heading">
                <h2>Customer balances</h2>
                <span className="muted small-text">
                  Current ledger balances
                </span>
              </div>
              <Table headers={["Customer", "Balance", "Unallocated payments"]}>
                {report.accounts.map((value) => (
                  <tr key={value.id}>
                    <td>{value.name}</td>
                    <td className="amount">{fmt(value.balance)}</td>
                    <td>{fmt(value.unallocated)}</td>
                  </tr>
                ))}
              </Table>
            </section>
          </div>
          <section className="panel space-top">
            <div className="panel-heading">
              <h2>Revenue & collections</h2>
              <p>Selected period · up to six recent months</p>
            </div>
            <RevenueChart report={report} fmt={fmt} />
          </section>
        </>
      ) : (
        <div className="loading-screen inline">
          <Loader2 className="spin" />
          Loading reports…
        </div>
      )}
    </>
  );
}

function SettingsPage({
  store,
  user,
  refresh,
  addUser,
  toggleUser,
  busy,
}: {
  store: Store;
  user: User;
  refresh: () => Promise<void>;
  addUser: () => void;
  toggleUser: (id: number) => void;
  busy: boolean;
}) {
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [saving, setSaving] = useState(false);
  const [currency, setCurrency] = useState(store.settings.currency);
  const [audit, setAudit] = useState<Activity[]>([]);
  useEffect(() => {
    if (!["admin", "manager"].includes(user.role)) return;
    api<Activity[]>("/audit")
      .then(setAudit)
      .catch((error) => setError(errorMessage(error)));
  }, [user.role]);
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setError("");
    setSuccess("");
    setSaving(true);
    try {
      await api("/settings", "PUT", {
        company: text(form, "company"),
        address: text(form, "address"),
        tax_id: text(form, "tax_id"),
        currency,
        decimals: ["KWD", "BHD", "OMR"].includes(currency) ? 3 : 2,
        tax_rate: text(form, "tax_rate"),
        payment_days: Number(text(form, "payment_days")),
        terms: text(form, "terms"),
        approval_threshold: text(form, "approval_threshold"),
        discount_threshold: text(form, "discount_threshold"),
        number_prefixes: {
          quote: text(form, "quote_prefix"),
          invoice: text(form, "invoice_prefix"),
          credit: text(form, "credit_prefix"),
          opening: text(form, "opening_prefix"),
          adjustment: text(form, "adjustment_prefix"),
          refund: text(form, "refund_prefix"),
        },
      });
      await refresh();
      setSuccess("Workspace settings saved.");
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setSaving(false);
    }
  }
  return (
    <>
      <section className="panel settings-panel">
        <div className="panel-heading">
          <div>
            <h2>Company & document settings</h2>
            <p>
              Configure your business identity, financial defaults and quote
              approvals.
            </p>
          </div>
        </div>
        <form onSubmit={save}>
          <fieldset disabled={user.role !== "admin" || saving}>
            <div className="form-grid">
              <Field label="Company name">
                <input
                  name="company"
                  required
                  defaultValue={store.settings.company}
                />
              </Field>
              <Field label="Tax registration number">
                <input name="tax_id" defaultValue={store.settings.tax_id} />
              </Field>
              <Field label="Company address" full>
                <textarea
                  name="address"
                  defaultValue={store.settings.address}
                  rows={2}
                />
              </Field>
              <Field label="Workspace currency">
                <select
                  value={currency}
                  onChange={(event) => setCurrency(event.target.value)}
                >
                  {[
                    "USD",
                    "EUR",
                    "GBP",
                    "KWD",
                    "AED",
                    "SAR",
                    "QAR",
                    "BHD",
                    "OMR",
                    "INR",
                  ].map((value) => (
                    <option key={value}>{value}</option>
                  ))}
                </select>
                <small>Currency is fixed once priced records exist.</small>
              </Field>
              <Field label="Default tax (%)">
                <input
                  name="tax_rate"
                  type="number"
                  step="any"
                  min="0"
                  max="100"
                  required
                  defaultValue={store.settings.tax_rate}
                />
              </Field>
              <Field label="Default payment terms (days)">
                <input
                  name="payment_days"
                  type="number"
                  min="0"
                  max="365"
                  required
                  defaultValue={store.settings.payment_days}
                />
              </Field>
              <Field label="Quote approval threshold">
                <input
                  name="approval_threshold"
                  type="number"
                  min="0"
                  step="any"
                  required
                  defaultValue={store.settings.approval_threshold}
                />
                <small>
                  Quotes at or above this amount require review. Set 0 to review
                  every quote.
                </small>
              </Field>
              <Field label="Discount requiring review above (%)">
                <input
                  name="discount_threshold"
                  type="number"
                  min="0"
                  max="100"
                  step="any"
                  required
                  defaultValue={store.settings.discount_threshold}
                />
              </Field>
              <Field label="Default document terms" full>
                <textarea
                  name="terms"
                  rows={3}
                  defaultValue={store.settings.terms}
                />
              </Field>
              {(
                [
                  "quote",
                  "invoice",
                  "credit",
                  "opening",
                  "adjustment",
                  "refund",
                ] as DocumentKind[]
              ).map((kind) => (
                <Field
                  key={kind}
                  label={`${kind[0].toUpperCase() + kind.slice(1)} number prefix`}
                >
                  <input
                    name={`${kind}_prefix`}
                    required
                    pattern="[A-Z]{2,8}"
                    maxLength={8}
                    defaultValue={store.settings.number_prefixes[kind]}
                  />
                  <small>
                    2–8 uppercase letters. Existing document numbers are
                    preserved.
                  </small>
                </Field>
              ))}
            </div>
            <div className="info-note">
              Invoices, credit notes, refunds, opening balances and adjustments
              always require a separate reviewer.
            </div>
            {user.role === "admin" && (
              <button className="button" disabled={saving}>
                {saving ? "Saving…" : "Save settings"}
              </button>
            )}
          </fieldset>
          {error && <div className="error">{error}</div>}
          {success && <div className="success">{success}</div>}
        </form>
      </section>
      <section className="panel space-top">
        <div className="panel-heading">
          <div>
            <h2>Team members</h2>
            <p>Admin, manager, sales and finance access</p>
          </div>
          {user.role === "admin" && (
            <button className="button secondary small" onClick={addUser}>
              <Plus size={15} />
              Add member
            </button>
          )}
        </div>
        <Table headers={["Name", "Email", "Role", "Status", ""]}>
          {store.users.map((member) => (
            <tr key={member.id}>
              <td>{member.name}</td>
              <td>{member.email}</td>
              <td>
                <Badge>{member.role}</Badge>
              </td>
              <td>
                <Badge tone={member.active ? "green" : ""}>
                  {member.active ? "Active" : "Inactive"}
                </Badge>
              </td>
              <td>
                {user.role === "admin" && member.id !== user.id && (
                  <button
                    className="text-button"
                    disabled={busy}
                    onClick={() => toggleUser(member.id)}
                  >
                    {member.active ? "Deactivate" : "Activate"}
                  </button>
                )}
              </td>
            </tr>
          ))}
        </Table>
      </section>
      {["admin", "manager"].includes(user.role) && (
        <section className="panel space-top">
          <div className="panel-heading">
            <h2>Audit history</h2>
            <Badge>Latest 200 events</Badge>
          </div>
          <Table headers={["Date", "Actor", "Action", "Details"]}>
            {audit.map((value) => (
              <tr key={value.id}>
                <td>{displayDate(value.created)}</td>
                <td>
                  {store.users.find((member) => member.id === value.actor_id)
                    ?.name ?? "System"}
                </td>
                <td>{value.action}</td>
                <td>{value.detail}</td>
              </tr>
            ))}
          </Table>
        </section>
      )}
    </>
  );
}
