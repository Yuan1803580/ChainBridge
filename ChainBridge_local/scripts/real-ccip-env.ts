import "dotenv/config";

import { getAddress, type Address } from "viem";

export function requiredEnv(name: string): string {
  const value = process.env[name];
  if (value === undefined || value.trim() === "") {
    throw new Error(`Missing required environment variable: ${name}`);
  }

  return value.trim();
}

export function optionalEnv(name: string): string | undefined {
  const value = process.env[name];
  if (value === undefined || value.trim() === "") {
    return undefined;
  }

  return value.trim();
}

export function requiredAddress(name: string): Address {
  return getAddress(requiredEnv(name));
}

export function optionalAddress(name: string): Address | undefined {
  const value = optionalEnv(name);
  if (value === undefined) {
    return undefined;
  }

  return getAddress(value);
}

export function requiredBigInt(name: string): bigint {
  return BigInt(requiredEnv(name));
}

export function optionalBigInt(name: string, fallback: bigint): bigint {
  const value = optionalEnv(name);
  if (value === undefined) {
    return fallback;
  }

  return BigInt(value);
}

