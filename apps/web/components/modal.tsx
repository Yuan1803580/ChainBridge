"use client";

import { X } from "lucide-react";
import { useEffect } from "react";

export function Modal({ open, title, description, onClose, children, width = "max-w-xl" }: { open: boolean; title: string; description?: string; onClose: () => void; children: React.ReactNode; width?: string }) {
  useEffect(() => {
    if (!open) return;
    const close = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [open, onClose]);
  if (!open) return null;
  return <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/40 p-4" role="dialog" aria-modal="true" aria-label={title}>
    <button className="absolute inset-0" onClick={onClose} aria-label="关闭对话框" />
    <div className={`relative max-h-[90vh] w-full ${width} overflow-auto rounded-md border border-line bg-white shadow-xl`}>
      <div className="sticky top-0 z-10 flex items-start justify-between border-b border-line bg-white px-5 py-4">
        <div><h2 className="text-base font-semibold">{title}</h2>{description && <p className="mt-1 text-xs leading-5 text-neutral-500">{description}</p>}</div>
        <button className="icon-btn size-8 border-0" onClick={onClose} aria-label="关闭"><X className="size-4" /></button>
      </div>
      <div className="p-5">{children}</div>
    </div>
  </div>;
}
