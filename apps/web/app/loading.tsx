import { LoaderCircle } from "lucide-react";

export default function Loading() {
  return <div className="grid min-h-[50vh] place-items-center"><div className="text-center text-sm text-neutral-500"><LoaderCircle className="mx-auto mb-3 size-6 animate-spin text-brand-600" />正在加载页面</div></div>;
}
