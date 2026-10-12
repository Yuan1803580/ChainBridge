import { ArrowLeft, SearchX } from "lucide-react";
import Link from "next/link";

export default function NotFound() {
  return <div className="surface grid min-h-[50vh] place-items-center px-6"><div className="text-center"><SearchX className="mx-auto mb-3 size-8 text-neutral-400" /><h1 className="text-lg font-semibold">没有找到这条记录</h1><p className="mt-2 text-sm text-neutral-500">记录可能已被删除，或链接中的 ID 不正确。</p><Link href="/" className="btn-secondary mt-5"><ArrowLeft className="size-4" />返回工作台</Link></div></div>;
}
