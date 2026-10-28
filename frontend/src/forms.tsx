import { useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { api, futureDate, text, today } from "./api";
import { Field, Modal } from "./ui";
import type {
  Customer,
  DocumentKind,
  Item,
  Opportunity,
  Payment,
  Product,
  SalesDocument,
  Store,
  User,
} from "./types";

interface Props {
  store: Store;
  user: User;
  close: () => void;
  refresh: () => Promise<void>;
}
function CustomerSelect({
  store,
  value,
  change,
  optional = false,
}: {
  store: Store;
  value?: string;
  change?: (value: string) => void;
  optional?: boolean;
}) {
  return (
    <select
      name="customer_id"
      required={!optional}
      value={value}
      onChange={change ? (event) => change(event.target.value) : undefined}
      defaultValue={value === undefined ? "" : undefined}
    >
      <option value="">
        {optional ? "No customer linked" : "Select a customer"}
      </option>
      {store.customers.map((value) => (
        <option key={value.id} value={value.id}>
          {value.name}
        </option>
      ))}
    </select>
  );
}
function OwnerSelect({
  store,
  user,
  selected,
}: {
  store: Store;
  user: User;
  selected?: number;
}) {
  return (
    <select name="owner_id" defaultValue={selected ?? user.id} required>
      {store.users
        .filter(
          (member) =>
            member.active &&
            member.role !== "finance" &&
            (user.role !== "sales" || member.id === user.id),
        )
        .map((member) => (
          <option key={member.id} value={member.id}>
            {member.name}
          </option>
        ))}
    </select>
  );
}

export function CustomerForm({
  store,
  close,
  refresh,
  customer,
}: Props & { customer?: Customer }) {
  const [contacts, setContacts] = useState(customer?.contacts ?? []);
  const [addresses, setAddresses] = useState(customer?.addresses ?? []);
  async function save(form: FormData) {
    const payload = {
      name: text(form, "name"),
      kind: text(form, "kind"),
      email: text(form, "email"),
      phone: text(form, "phone"),
      contact: text(form, "contact"),
      address: text(form, "address"),
      tax_id: text(form, "tax_id"),
      tags: text(form, "tags"),
      payment_days: Number(text(form, "payment_days")),
      notes: text(form, "notes"),
      contacts,
      addresses,
    };
    await api(
      customer ? `/customers/${customer.id}` : "/customers",
      customer ? "PUT" : "POST",
      payload,
    );
    await refresh();
  }
  return (
    <Modal
      title={customer ? "Edit customer" : "New customer"}
      subtitle="Keep every customer relationship in one place."
      close={close}
      save={save}
      wide
    >
      <div className="form-grid">
        <Field label="Customer / company name">
          <input
            name="name"
            required
            defaultValue={customer?.name}
            maxLength={200}
            placeholder="e.g. Atlas Trading Co."
          />
        </Field>
        <Field label="Customer type">
          <select name="kind" defaultValue={customer?.kind ?? "company"}>
            <option value="company">Company</option>
            <option value="individual">Individual</option>
          </select>
        </Field>
        <Field label="Main contact">
          <input name="contact" defaultValue={customer?.contact} />
        </Field>
        <Field label="Email">
          <input type="email" name="email" defaultValue={customer?.email} />
        </Field>
        <Field label="Phone">
          <input name="phone" defaultValue={customer?.phone} />
        </Field>
        <Field label="Tax registration number">
          <input name="tax_id" defaultValue={customer?.tax_id} />
        </Field>
        <Field label="Payment terms (days)">
          <input
            type="number"
            name="payment_days"
            min={0}
            max={365}
            required
            defaultValue={customer?.payment_days ?? store.settings.payment_days}
          />
        </Field>
        <Field label="Tags (comma separated)">
          <input
            name="tags"
            defaultValue={customer?.tags}
            placeholder="VIP, Technology"
          />
        </Field>
        <Field label="Address" full>
          <textarea name="address" defaultValue={customer?.address} rows={2} />
        </Field>
        <Field label="Notes" full>
          <textarea name="notes" defaultValue={customer?.notes} rows={3} />
        </Field>
      </div>
      <div className="section-heading">
        <h3>Additional contacts</h3>
        <button
          type="button"
          className="text-button"
          onClick={() =>
            setContacts([...contacts, { name: "", email: "", phone: "" }])
          }
        >
          <Plus size={15} />
          Add contact
        </button>
      </div>
      {contacts.map((contact, index) => (
        <div className="contact-row" key={index}>
          <input
            required
            aria-label="Contact name"
            placeholder="Name"
            value={contact.name}
            onChange={(event) =>
              setContacts(
                contacts.map((value, i) =>
                  i === index ? { ...value, name: event.target.value } : value,
                ),
              )
            }
          />
          <input
            type="email"
            aria-label="Contact email"
            placeholder="Email"
            value={contact.email}
            onChange={(event) =>
              setContacts(
                contacts.map((value, i) =>
                  i === index ? { ...value, email: event.target.value } : value,
                ),
              )
            }
          />
          <input
            aria-label="Contact phone"
            placeholder="Phone"
            value={contact.phone}
            onChange={(event) =>
              setContacts(
                contacts.map((value, i) =>
                  i === index ? { ...value, phone: event.target.value } : value,
                ),
              )
            }
          />
          <button
            type="button"
            aria-label="Remove contact"
            className="icon-button"
            onClick={() => setContacts(contacts.filter((_, i) => i !== index))}
          >
            <Trash2 size={16} />
          </button>
        </div>
      ))}
      <div className="section-heading">
        <h3>Additional addresses</h3>
        <button
          type="button"
          className="text-button"
          onClick={() =>
            setAddresses([...addresses, { label: "", address: "" }])
          }
        >
          <Plus size={15} />
          Add address
        </button>
      </div>
      {addresses.map((address, index) => (
        <div className="contact-row" key={index}>
          <input
            required
            aria-label="Address label"
            placeholder="e.g. Shipping"
            maxLength={100}
            value={address.label}
            onChange={(event) =>
              setAddresses(
                addresses.map((value, i) =>
                  i === index ? { ...value, label: event.target.value } : value,
                ),
              )
            }
          />
          <input
            required
            aria-label="Additional address"
            placeholder="Full address"
            maxLength={4000}
            value={address.address}
            onChange={(event) =>
              setAddresses(
                addresses.map((value, i) =>
                  i === index
                    ? { ...value, address: event.target.value }
                    : value,
                ),
              )
            }
          />
          <button
            type="button"
            className="icon-button"
            aria-label="Remove address"
            onClick={() =>
              setAddresses(addresses.filter((_, i) => i !== index))
            }
          >
            <Trash2 size={16} />
          </button>
        </div>
      ))}
    </Modal>
  );
}

export function DocumentForm({
  store,
  close,
  refresh,
  kind,
  existing,
}: Props & { kind: DocumentKind; existing?: SalesDocument }) {
  const [items, setItems] = useState<Item[]>(
    existing?.items.length
      ? existing.items
      : [{ description: "", quantity: "1", unit_price: "" }],
  );
  const [customerId, setCustomerId] = useState(
    String(existing?.customer_id ?? ""),
  );
  const financial = !["quote", "invoice"].includes(kind);
  const title = {
    quote: "quote",
    invoice: "invoice",
    credit: "credit note",
    opening: "opening balance",
    adjustment: "account adjustment",
    refund: "customer refund",
  }[kind];
  const invoices = store.documents.filter(
    (value) =>
      value.customer_id === Number(customerId) &&
      value.kind === "invoice" &&
      value.status === "issued",
  );
  const changeItem = (index: number, key: keyof Item, value: string) =>
    setItems(
      items.map((item, i) => (i === index ? { ...item, [key]: value } : item)),
    );
  async function save(form: FormData) {
    const amount = text(form, "amount") || "0";
    const payload = {
      kind,
      customer_id: Number(customerId),
      invoice_id: kind === "credit" ? Number(text(form, "invoice_id")) : null,
      date: text(form, "date"),
      due: text(form, "due") || text(form, "date"),
      items: financial ? [] : items,
      discount_rate: financial ? "0" : text(form, "discount_rate"),
      tax_rate: financial ? "0" : text(form, "tax_rate"),
      amount: text(form, "direction") === "credit" ? `-${amount}` : amount,
      terms: text(form, "terms"),
      reason: text(form, "reason"),
    };
    await api(
      existing ? `/documents/${existing.id}` : "/documents",
      existing ? "PUT" : "POST",
      payload,
    );
    await refresh();
  }
  return (
    <Modal
      title={`${existing ? "Revise" : "New"} ${title}`}
      subtitle={
        financial
          ? "This transaction affects the ledger only after approval."
          : "Save a draft, then submit it for approval."
      }
      close={close}
      save={save}
      submitLabel="Save draft"
      wide
    >
      <div className="form-grid">
        <Field label="Customer">
          <CustomerSelect
            store={store}
            value={customerId}
            change={setCustomerId}
          />
        </Field>
        <Field label="Document date">
          <input
            type="date"
            name="date"
            required
            max={today()}
            defaultValue={existing?.date ?? today()}
          />
        </Field>
        {!financial && (
          <Field label={kind === "quote" ? "Valid until" : "Payment due date"}>
            <input
              type="date"
              name="due"
              required
              defaultValue={
                existing?.due ??
                futureDate(
                  store.customers.find(
                    (value) => value.id === Number(customerId),
                  )?.payment_days ?? store.settings.payment_days,
                )
              }
            />
          </Field>
        )}
        {financial && (
          <Field label={`Amount (${store.settings.currency})`}>
            <input
              name="amount"
              type="number"
              min="0.001"
              step="any"
              required
              defaultValue={
                existing ? Math.abs(Number(existing.total)) : undefined
              }
            />
          </Field>
        )}
        {["adjustment", "opening"].includes(kind) && (
          <Field label="Balance direction">
            <select
              name="direction"
              defaultValue={
                existing && Number(existing.total) < 0 ? "credit" : "debit"
              }
            >
              <option value="debit">Debit — customer owes us</option>
              <option value="credit">Credit — customer has credit</option>
            </select>
          </Field>
        )}
        {kind === "credit" && (
          <Field label="Invoice to credit">
            <select
              name="invoice_id"
              required
              defaultValue={existing?.invoice_id ?? ""}
            >
              <option value="">Select an issued invoice</option>
              {invoices.map((invoice) => (
                <option key={invoice.id} value={invoice.id}>
                  {invoice.number} · {invoice.total} original total
                </option>
              ))}
            </select>
          </Field>
        )}
        {financial && (
          <Field label="Reason" full>
            <textarea
              name="reason"
              required
              defaultValue={existing?.reason}
              rows={3}
            />
          </Field>
        )}
      </div>
      {!financial && (
        <>
          <div className="section-heading">
            <h3>Line items</h3>
            <select
              className="catalogue-select"
              value=""
              onChange={(event) => {
                const product = store.products.find(
                  (value) => value.id === Number(event.target.value),
                );
                if (product) {
                  const item = {
                    description: product.description || product.name,
                    quantity: "1",
                    unit_price: product.price,
                  };
                  setItems(
                    items.length === 1 && !items[0].description
                      ? [item]
                      : [...items, item],
                  );
                }
              }}
            >
              <option value="">Add from catalogue</option>
              {store.products
                .filter((value) => value.active)
                .map((value) => (
                  <option key={value.id} value={value.id}>
                    {value.name}
                  </option>
                ))}
            </select>
          </div>
          <div className="line-labels">
            <span>Description</span>
            <span>Qty</span>
            <span>Unit price</span>
            <span />
          </div>
          {items.map((item, index) => (
            <div className="line-item" key={index}>
              <input
                aria-label={`Item ${index + 1} description`}
                placeholder="Product or service"
                required
                value={item.description}
                onChange={(event) =>
                  changeItem(index, "description", event.target.value)
                }
              />
              <input
                aria-label={`Item ${index + 1} quantity`}
                type="number"
                min="0.0001"
                step="0.0001"
                required
                value={item.quantity}
                onChange={(event) =>
                  changeItem(index, "quantity", event.target.value)
                }
              />
              <input
                aria-label={`Item ${index + 1} unit price`}
                type="number"
                min="0"
                step="any"
                required
                value={item.unit_price}
                onChange={(event) =>
                  changeItem(index, "unit_price", event.target.value)
                }
              />
              <button
                type="button"
                className="icon-button"
                aria-label="Remove line item"
                disabled={items.length === 1}
                onClick={() => setItems(items.filter((_, i) => i !== index))}
              >
                <Trash2 size={16} />
              </button>
            </div>
          ))}
          <button
            type="button"
            className="text-button"
            onClick={() =>
              setItems([
                ...items,
                { description: "", quantity: "1", unit_price: "" },
              ])
            }
          >
            <Plus size={16} />
            Add line item
          </button>
          <div className="form-grid space-top">
            <Field label="Discount (%)">
              <input
                name="discount_rate"
                type="number"
                min="0"
                max="100"
                step="any"
                required
                defaultValue={existing?.discount_rate ?? "0"}
              />
            </Field>
            <Field label="Tax (%)">
              <input
                name="tax_rate"
                type="number"
                min="0"
                max="100"
                step="any"
                required
                defaultValue={existing?.tax_rate ?? store.settings.tax_rate}
              />
            </Field>
            <Field label="Terms and conditions" full>
              <textarea
                name="terms"
                rows={3}
                defaultValue={existing?.terms ?? store.settings.terms}
              />
            </Field>
          </div>
          <div className="info-note">
            Totals are calculated and rounded by the server in{" "}
            {store.settings.currency}. Drafts do not change customer balances.
          </div>
        </>
      )}
    </Modal>
  );
}

export function PaymentForm({
  store,
  close,
  refresh,
  payment,
}: Props & { payment?: Payment }) {
  const [customerId, setCustomerId] = useState(
    String(payment?.customer_id ?? ""),
  );
  const [allocations, setAllocations] = useState<Record<number, string>>({});
  const [key] = useState(crypto.randomUUID());
  const invoices = store.documents.filter(
    (value) =>
      value.customer_id === Number(customerId) &&
      value.kind === "invoice" &&
      value.status === "issued" &&
      Number(value.outstanding) > 0,
  );
  async function save(form: FormData) {
    const entries = Object.entries(allocations)
      .filter(([, value]) => Number(value) > 0)
      .map(([id, amount]) => ({ invoice_id: Number(id), amount }));
    if (payment) await api(`/payments/${payment.id}/allocate`, "POST", entries);
    else
      await api("/payments", "POST", {
        customer_id: Number(customerId),
        amount: text(form, "amount"),
        date: text(form, "date"),
        reference: text(form, "reference"),
        method: text(form, "method"),
        idempotency_key: key,
        allocations: entries,
      });
    await refresh();
  }
  return (
    <Modal
      title={payment ? "Allocate customer payment" : "Record customer payment"}
      subtitle={
        payment
          ? `${payment.reference} · Unallocated ${payment.unallocated} ${store.settings.currency}`
          : "Allocate across invoices, or keep the payment as customer credit."
      }
      close={close}
      save={save}
      wide
    >
      <div className="form-grid">
        <Field label="Customer">
          {payment ? (
            <input
              readOnly
              value={
                store.customers.find(
                  (value) => value.id === payment.customer_id,
                )?.name ?? ""
              }
            />
          ) : (
            <CustomerSelect
              store={store}
              value={customerId}
              change={(value) => {
                setCustomerId(value);
                setAllocations({});
              }}
            />
          )}
        </Field>
        {!payment && (
          <>
            <Field label={`Amount (${store.settings.currency})`}>
              <input
                name="amount"
                type="number"
                min="0.001"
                step="any"
                required
              />
            </Field>
            <Field label="Payment date">
              <input
                name="date"
                type="date"
                required
                max={today()}
                defaultValue={today()}
              />
            </Field>
            <Field label="Payment method">
              <select name="method">
                {["Bank transfer", "Cash", "Card", "Cheque", "Other"].map(
                  (value) => (
                    <option key={value}>{value}</option>
                  ),
                )}
              </select>
            </Field>
            <Field label="Receipt / bank reference" full>
              <input
                name="reference"
                required
                placeholder="Bank reference or receipt number"
              />
            </Field>
          </>
        )}
      </div>
      <h3 className="space-top">Invoice allocations</h3>
      {invoices.length ? (
        invoices.map((invoice) => (
          <div className="allocation-row" key={invoice.id}>
            <div>
              <strong>{invoice.number}</strong>
              <small>
                Outstanding: {invoice.outstanding} {invoice.currency}
              </small>
            </div>
            <input
              aria-label={`Allocate to ${invoice.number}`}
              type="number"
              step="any"
              min="0"
              max={invoice.outstanding}
              placeholder="0.00"
              value={allocations[invoice.id] ?? ""}
              onChange={(event) =>
                setAllocations({
                  ...allocations,
                  [invoice.id]: event.target.value,
                })
              }
            />
          </div>
        ))
      ) : (
        <p className="muted">No outstanding invoices for this customer.</p>
      )}
      <div className="info-note">
        Any unallocated amount remains customer credit. Recording a payment
        immediately updates the account ledger.
      </div>
    </Modal>
  );
}

export function OpportunityForm({
  store,
  user,
  close,
  refresh,
  existing,
}: Props & { existing?: Opportunity }) {
  async function save(form: FormData) {
    const payload = {
      title: text(form, "title"),
      customer_id: Number(text(form, "customer_id")) || null,
      owner_id: Number(text(form, "owner_id")),
      source: text(form, "source"),
      stage: text(form, "stage"),
      value: text(form, "value"),
      closing_date: text(form, "closing_date") || null,
      notes: text(form, "notes"),
    };
    await api(
      existing ? `/opportunities/${existing.id}` : "/opportunities",
      existing ? "PUT" : "POST",
      payload,
    );
    await refresh();
  }
  return (
    <Modal
      title={existing ? "Edit opportunity" : "New opportunity"}
      close={close}
      save={save}
    >
      <div className="form-grid">
        <Field label="Opportunity title" full>
          <input
            name="title"
            required
            defaultValue={existing?.title}
            placeholder="e.g. Annual support contract"
          />
        </Field>
        <Field label="Customer">
          <select name="customer_id" defaultValue={existing?.customer_id ?? ""}>
            <option value="">No customer linked</option>
            {store.customers.map((value) => (
              <option key={value.id} value={value.id}>
                {value.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Assigned to">
          <OwnerSelect
            store={store}
            user={user}
            selected={existing?.owner_id}
          />
        </Field>
        <Field label="Lead source">
          <input
            name="source"
            required
            defaultValue={existing?.source ?? "Website"}
          />
        </Field>
        <Field label="Sales stage">
          <select name="stage" defaultValue={existing?.stage ?? "New"}>
            {["New", "Qualified", "Proposal", "Negotiation", "Won", "Lost"].map(
              (value) => (
                <option key={value}>{value}</option>
              ),
            )}
          </select>
        </Field>
        <Field label={`Expected value (${store.settings.currency})`}>
          <input
            name="value"
            type="number"
            step="any"
            min="0"
            required
            defaultValue={existing?.value ?? "0"}
          />
        </Field>
        <Field label="Expected closing date">
          <input
            name="closing_date"
            type="date"
            defaultValue={existing?.closing_date}
          />
        </Field>
        <Field label="Notes" full>
          <textarea name="notes" defaultValue={existing?.notes} />
        </Field>
      </div>
    </Modal>
  );
}

export function TaskForm({ store, user, close, refresh }: Props) {
  async function save(form: FormData) {
    await api("/tasks", "POST", {
      title: text(form, "title"),
      customer_id: Number(text(form, "customer_id")) || null,
      owner_id: Number(text(form, "owner_id")),
      due: text(form, "due"),
      notes: text(form, "notes"),
    });
    await refresh();
  }
  return (
    <Modal title="Schedule a follow-up" close={close} save={save}>
      <div className="form-grid">
        <Field label="Task" full>
          <input
            name="title"
            required
            placeholder="What needs to happen next?"
          />
        </Field>
        <Field label="Customer">
          <CustomerSelect store={store} optional />
        </Field>
        <Field label="Assigned to">
          <OwnerSelect store={store} user={user} />
        </Field>
        <Field label="Due date">
          <input name="due" type="date" required defaultValue={today()} />
        </Field>
        <Field label="Notes" full>
          <textarea name="notes" />
        </Field>
      </div>
    </Modal>
  );
}

export function ProductForm({
  close,
  refresh,
  existing,
  store,
}: Props & { existing?: Product }) {
  async function save(form: FormData) {
    await api(
      existing ? `/products/${existing.id}` : "/products",
      existing ? "PUT" : "POST",
      {
        name: text(form, "name"),
        description: text(form, "description"),
        unit: text(form, "unit"),
        price: text(form, "price"),
        active: text(form, "active") === "true",
      },
    );
    await refresh();
  }
  return (
    <Modal
      title={existing ? "Edit catalogue item" : "New product or service"}
      close={close}
      save={save}
    >
      <div className="form-grid">
        <Field label="Name" full>
          <input name="name" required defaultValue={existing?.name} />
        </Field>
        <Field label={`Unit price (${store.settings.currency})`}>
          <input
            name="price"
            type="number"
            min="0"
            step="any"
            required
            defaultValue={existing?.price}
          />
        </Field>
        <Field label="Unit">
          <input name="unit" required defaultValue={existing?.unit ?? "item"} />
        </Field>
        <Field label="Availability">
          <select name="active" defaultValue={String(existing?.active ?? true)}>
            <option value="true">Active</option>
            <option value="false">Archived</option>
          </select>
        </Field>
        <Field label="Description" full>
          <textarea name="description" defaultValue={existing?.description} />
        </Field>
      </div>
    </Modal>
  );
}

export function UserForm({ close, refresh }: Props) {
  async function save(form: FormData) {
    await api("/users", "POST", {
      name: text(form, "name"),
      email: text(form, "email"),
      password: text(form, "password"),
      role: text(form, "role"),
    });
    await refresh();
  }
  return (
    <Modal
      title="Add a team member"
      subtitle="Each person has a separate account and permissions."
      close={close}
      save={save}
    >
      <div className="form-grid">
        <Field label="Full name" full>
          <input name="name" required />
        </Field>
        <Field label="Email" full>
          <input name="email" type="email" required />
        </Field>
        <Field label="Initial password" full>
          <input
            name="password"
            type="password"
            required
            minLength={12}
            autoComplete="new-password"
          />
          <small>At least 12 characters.</small>
        </Field>
        <Field label="Role" full>
          <select name="role">
            <option value="sales">
              Sales — customers, own pipeline and quotes
            </option>
            <option value="finance">
              Finance — invoices, payments and accounts
            </option>
            <option value="manager">
              Manager — sales, approvals and reports
            </option>
            <option value="admin">
              Admin — all modules and workspace settings
            </option>
          </select>
        </Field>
      </div>
    </Modal>
  );
}
