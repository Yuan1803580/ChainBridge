const stateLabels: Record<string, string> = {
  draft: "草稿", published: "竞标中", awarded: "已选标", closed: "已关闭", cancelled: "已取消",
  pending: "待处理", accepted: "已接受", rejected: "已拒绝", withdrawn: "已撤回",
  awaiting_funding: "待锁款", funded: "已锁款", in_progress: "执行中", delivered: "待验收",
  completed: "已完成", disputed: "争议中", refund_pending: "退款处理中", refunded: "已退款",
  created: "已创建", locked: "已锁定", release_pending: "放款处理中", released: "已放款", failed: "失败",
  active: "活跃", suspended: "已停用", valid: "有效", expired: "已过期", revoked: "已撤销",
  online: "在线", busy: "忙碌", offline: "离线", available: "可用"
};

export function stateLabel(state: string) {
  return stateLabels[state] || state;
}

export function formatDate(value: string, withTime = true) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "short", day: "numeric", ...(withTime ? { hour: "2-digit", minute: "2-digit", hour12: false } : {})
  }).format(new Date(value));
}

export function formatMoney(amount: string, currency: string) {
  return `${amount} ${currency}`;
}

export function shortDid(did: string, length = 22) {
  return did.length > length ? `${did.slice(0, length)}…` : did;
}

export function shortHash(hash: string, length = 16) {
  return hash.length > length ? `${hash.slice(0, 10)}…${hash.slice(-4)}` : hash;
}

export function cnRole(role: string) {
  return ({ requester: "需求方", provider: "供给方", admin: "管理员" } as Record<string, string>)[role] || role;
}
