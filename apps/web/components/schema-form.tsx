"use client";

import { useMemo, useState } from "react";
import type { JsonSchema, JsonSchemaProperty } from "@/lib/types";

function initialValue(property: JsonSchemaProperty): unknown {
  if (property.default !== undefined) return property.default;
  if (property.type === "boolean") return false;
  if (property.type === "array") return [];
  if (property.type === "number" || property.type === "integer") return "";
  return "";
}

function convert(value: unknown, property: JsonSchemaProperty) {
  if (property.type === "number" || property.type === "integer") return value === "" ? undefined : Number(value);
  if (property.type === "array" && typeof value === "string") {
    return value.split(",").map((item) => item.trim()).filter(Boolean).map((item) => property.items?.type === "number" || property.items?.type === "integer" ? Number(item) : item);
  }
  return value;
}

export function SchemaForm({ schema, onSubmit, submitLabel = "运行接口", disabled }: { schema: JsonSchema; onSubmit: (value: Record<string, unknown>) => Promise<void> | void; submitLabel?: string; disabled?: boolean }) {
  const defaults = useMemo(() => Object.fromEntries(Object.entries(schema.properties).map(([key, prop]) => [key, initialValue(prop)])), [schema]);
  const [values, setValues] = useState<Record<string, unknown>>(defaults);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const nextErrors: Record<string, string> = {};
    for (const required of schema.required || []) {
      const value = values[required];
      if (value === "" || value === undefined || (Array.isArray(value) && value.length === 0)) nextErrors[required] = "此字段为必填项";
    }
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length) return;
    setSubmitting(true);
    try {
      const converted = Object.fromEntries(Object.entries(values).map(([key, value]) => [key, convert(value, schema.properties[key])]));
      await onSubmit(converted);
    } finally { setSubmitting(false); }
  };

  return <form onSubmit={submit} className="space-y-4">
    {Object.entries(schema.properties).map(([name, property]) => {
      const required = schema.required?.includes(name);
      const common = { id: `field-${name}`, name, disabled: submitting || disabled, "aria-invalid": Boolean(errors[name]), value: Array.isArray(values[name]) ? (values[name] as unknown[]).join(", ") : String(values[name] ?? ""), onChange: (event: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => setValues({ ...values, [name]: event.target.value }) };
      return <div key={name}>
        <label className="label" htmlFor={`field-${name}`}>{property.title || name}{required && <span className="ml-1 text-red-600">*</span>}</label>
        {property.enum ? <select className="select w-full" {...common}>{property.enum.map((option) => <option key={String(option)} value={String(option)}>{String(option)}</option>)}</select>
          : property.type === "string" && (name === "text" || name === "content" || name === "code") ? <textarea className="textarea" placeholder={property.description} {...common} />
          : <input className="input" type={property.type === "number" || property.type === "integer" ? "number" : "text"} min={property.minimum} max={property.maximum} placeholder={property.type === "array" ? "使用逗号分隔多个值" : property.description} {...common} />}
        <div className="mt-1 flex justify-between gap-3 text-[11px]"><span className={errors[name] ? "text-red-600" : "text-neutral-400"}>{errors[name] || property.description}</span><span className="shrink-0 font-mono text-neutral-400">{property.type}</span></div>
      </div>;
    })}
    <button className="btn-primary w-full" disabled={submitting || disabled}>{submitting ? "正在提交…" : submitLabel}</button>
  </form>;
}
