import type { Address } from "viem";

export type RouteContext = {
  routeId: string;
  routeVersion: number;
  sourceChainId: number;
  destinationChainId: number;
  sourceRpcUrl: string;
  destinationRpcUrl: string;
  sourceGatewayAddress: Address;
  remoteWorkBoardAddress: Address;
  active: boolean;
};

const addressPattern = /^0x[0-9a-fA-F]{40}$/;

function integer(value: unknown, field: string): number {
  const result = Number(value);
  if (!Number.isInteger(result) || result <= 0) throw new Error(`${field} must be a positive integer`);
  return result;
}

function string(value: unknown, field: string): string {
  if (typeof value !== "string" || !value.trim()) throw new Error(`${field} is required`);
  return value.trim();
}

function address(value: unknown, field: string): Address {
  const result = string(value, field);
  if (!addressPattern.test(result)) throw new Error(`${field} must be an EVM address`);
  return result as Address;
}

export function routeIdentity(routeId: string, routeVersion: number): string {
  return `${routeId}:${routeVersion}`;
}

export function parseRouteContexts(raw: string): RouteContext[] {
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch (error) {
    throw new Error(`CHAIN_ROUTES_JSON is invalid JSON: ${error instanceof Error ? error.message : String(error)}`);
  }
  if (!Array.isArray(value) || value.length === 0) {
    throw new Error("CHAIN_ROUTES_JSON must be a non-empty array");
  }
  const seen = new Set<string>();
  return value.map((item, index) => {
    if (!item || typeof item !== "object") throw new Error(`route[${index}] must be an object`);
    const source = item as Record<string, unknown>;
    const route: RouteContext = {
      routeId: string(source.routeId, `route[${index}].routeId`),
      routeVersion: integer(source.routeVersion, `route[${index}].routeVersion`),
      sourceChainId: integer(source.sourceChainId, `route[${index}].sourceChainId`),
      destinationChainId: integer(source.destinationChainId, `route[${index}].destinationChainId`),
      sourceRpcUrl: string(source.sourceRpcUrl, `route[${index}].sourceRpcUrl`),
      destinationRpcUrl: string(source.destinationRpcUrl, `route[${index}].destinationRpcUrl`),
      sourceGatewayAddress: address(source.sourceGatewayAddress, `route[${index}].sourceGatewayAddress`),
      remoteWorkBoardAddress: address(source.remoteWorkBoardAddress, `route[${index}].remoteWorkBoardAddress`),
      active: source.active !== false,
    };
    if (route.sourceChainId === route.destinationChainId) {
      throw new Error(`route[${index}] source and destination chain IDs must differ`);
    }
    const identity = routeIdentity(route.routeId, route.routeVersion);
    if (seen.has(identity)) throw new Error(`Duplicate route context ${identity}`);
    seen.add(identity);
    return route;
  });
}

export class RouteContextRegistry {
  private contexts = new Map<string, RouteContext>();

  constructor(routes: RouteContext[]) {
    if (routes.length === 0) throw new Error("At least one route context is required");
    for (const route of routes) {
      const key = routeIdentity(route.routeId, route.routeVersion);
      if (this.contexts.has(key)) throw new Error(`Duplicate route context ${key}`);
      this.contexts.set(key, { ...route });
    }
  }

  resolve(routeId: string, routeVersion: number): RouteContext {
    const route = this.contexts.get(routeIdentity(routeId, routeVersion));
    if (!route || !route.active) throw new Error(`Active route context ${routeId}:${routeVersion} was not found`);
    return { ...route };
  }

  latest(routeId: string): RouteContext {
    const candidates = [...this.contexts.values()]
      .filter((route) => route.routeId === routeId && route.active)
      .sort((left, right) => right.routeVersion - left.routeVersion);
    if (candidates.length === 0) throw new Error(`No active route context exists for ${routeId}`);
    return { ...candidates[0] };
  }

  list(): RouteContext[] {
    return [...this.contexts.values()]
      .map((route) => ({ ...route }))
      .sort((left, right) => left.destinationChainId - right.destinationChainId || left.routeVersion - right.routeVersion);
  }
}
