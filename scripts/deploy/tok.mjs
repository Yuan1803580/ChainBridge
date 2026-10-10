import { createPublicClient, http } from "viem";
import { sepolia, arbitrumSepolia } from "viem/chains";
const tok = "0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238";
const abi = [
  {type:"function",name:"decimals",inputs:[],outputs:[{type:"uint8"}],stateMutability:"view"},
  {type:"function",name:"symbol",inputs:[],outputs:[{type:"string"}],stateMutability:"view"},
  {type:"function",name:"name",inputs:[],outputs:[{type:"string"}],stateMutability:"view"}
];
const sep = createPublicClient({ chain: sepolia, transport: http(process.env.SOURCE_RPC_URL) });
try {
  const d = await sep.readContract({ address: tok, abi, functionName: "decimals" });
  const s = await sep.readContract({ address: tok, abi, functionName: "symbol" });
  const n = await sep.readContract({ address: tok, abi, functionName: "name" });
  console.log("SOURCE_DECIMALS", d, "SOURCE_SYMBOL", s, "SOURCE_NAME", n);
} catch (e) { console.log("SRC_ERR", e.message); }
const dst = "0x75faf114eafb1BDbe2F0316DF893fd58CE46AA4d";
const arb = createPublicClient({ chain: arbitrumSepolia, transport: http(process.env.DESTINATION_RPC_URL) });
try {
  const d = await arb.readContract({ address: dst, abi, functionName: "decimals" });
  const s = await arb.readContract({ address: dst, abi, functionName: "symbol" });
  const n = await arb.readContract({ address: dst, abi, functionName: "name" });
  console.log("DEST_DECIMALS", d, "DEST_SYMBOL", s, "DEST_NAME", n);
} catch (e) { console.log("DST_ERR", e.message); }
