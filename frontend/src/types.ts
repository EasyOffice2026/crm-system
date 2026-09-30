export interface User {
  id: number;
  name: string;
  email: string;
  role: "admin" | "manager" | "sales" | "finance";
  active: boolean;
}
export interface Customer {
  id: number;
  name: string;
  kind: string;
  email: string;
  phone: string;
  contact: string;
  contacts: { name: string; email: string; phone: string }[];
  addresses: { label: string; address: string }[];
  address: string;
  tax_id: string;
  tags: string;
  payment_days: number;
  notes: string;
  balance: string;
  unallocated: string;
}
export interface Product {
  id: number;
  name: string;
  description: string;
  unit: string;
  price: string;
  active: boolean;
}
export interface Opportunity {
  id: number;
  title: string;
  customer_id: number | null;
  owner_id: number;
  source: string;
  stage: string;
  value: string;
  closing_date: string;
  notes: string;
}
export interface Task {
  id: number;
  title: string;
  customer_id: number | null;
  owner_id: number;
  due: string;
  done: boolean;
  notes: string;
}
export interface Item {
  description: string;
  quantity: string;
  unit_price: string;
}
export type DocumentKind =
  | "quote"
  | "invoice"
  | "credit"
  | "opening"
  | "adjustment"
  | "refund";
export interface SalesDocument {
  id: number;
  number: string;
  kind: DocumentKind;
  customer_id: number;
  customer: string;
  author_id: number;
  status: string;
  revision: number;
  date: string;
  due: string;
  items: Item[];
  subtotal: string;
  discount_rate: string;
  discount: string;
  tax_rate: string;
  tax: string;
  total: string;
  currency: string;
  decimals: number;
  terms: string;
  reason: string;
  quote_id: number | null;
  invoice_id: number | null;
  outstanding: string;
  payment_state: string;
}
export interface Approval {
  id: number;
  document_id: number;
  requested_by: number;
  status: string;
  reviewer_id: number | null;
  comment: string;
  created: string;
  revision: number;
  requester: string;
  document: SalesDocument;
}
export interface Payment {
  id: number;
  customer_id: number;
  amount: string;
  date: string;
  reference: string;
  method: string;
  unallocated: string;
  allocations: { invoice_id: number; amount: string }[];
}
export interface Settings {
  company: string;
  address: string;
  tax_id: string;
  currency: string;
  decimals: number;
  tax_rate: string;
  payment_days: number;
  terms: string;
  approval_threshold: string;
  discount_threshold: string;
  number_prefixes: Record<DocumentKind, string>;
}
export interface Activity {
  id: number;
  actor_id: number;
  customer_id: number | null;
  action: string;
  detail: string;
  created: string;
}
export interface Ledger {
  customer: string;
  opening: string;
  balance: string;
  unallocated: string;
  entries: {
    id: number;
    date: string;
    description: string;
    debit: string;
    credit: string;
    balance: string;
  }[];
}
export interface Report {
  invoiced: string;
  credited: string;
  net_sales: string;
  collected: string;
  refunded: string;
  net_collections: string;
  outstanding: string;
  ageing: string[];
  accounts: {
    id: number;
    name: string;
    balance: string;
    unallocated: string;
  }[];
  salespeople: {
    name: string;
    deals: number;
    won: number;
    won_value: string;
  }[];
  open_invoices: SalesDocument[];
  quote_conversion: number;
  trend: { month: string; invoiced: string; collected: string }[];
  receivables_as_of: string;
}
export interface Dashboard {
  customers: number;
  pipeline: string;
  overdue_tasks: number;
  pending_approvals: number;
  financial: Report | null;
  activities: Activity[];
  company: string;
}
export interface Store {
  customers: Customer[];
  products: Product[];
  opportunities: Opportunity[];
  tasks: Task[];
  documents: SalesDocument[];
  approvals: Approval[];
  users: User[];
  payments: Payment[];
  settings: Settings;
  dashboard: Dashboard;
}
