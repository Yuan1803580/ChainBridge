import hardhatToolboxViemPlugin from "@nomicfoundation/hardhat-toolbox-viem";
import { configVariable, defineConfig } from "hardhat/config";
import { definePlugin } from "hardhat/plugins";
import path from "node:path";

const projectDirectory = import.meta.dirname;
const hardhatRoot = process.env.HARDHAT_PROJECT_ROOT || projectDirectory;
const projectRemappingsPlugin = definePlugin({
  id: "local:project-remappings",
  hookHandlers: {
    solidity: async () => ({
      default: async () => ({
        readNpmPackageRemappings: async (
          context,
          packageName,
          packageVersion,
          packagePath,
          next,
        ) => {
          if (path.resolve(packagePath) === path.resolve(projectDirectory)) {
            return [];
          }
          return next(context, packageName, packageVersion, packagePath);
        },
      }),
    }),
  },
});

export default defineConfig({
  plugins: [hardhatToolboxViemPlugin, projectRemappingsPlugin],
  paths: {
    root: hardhatRoot,
    sources: { solidity: path.join(projectDirectory, "contracts") },
    tests: { nodejs: path.join(projectDirectory, "test") },
    cache: path.join(projectDirectory, "hardhat-cache"),
    artifacts: path.join(projectDirectory, "hardhat-artifacts"),
  },
  solidity: {
    profiles: {
      default: {
        version: "0.8.28",
      },
      production: {
        version: "0.8.28",
        settings: {
          optimizer: {
            enabled: true,
            runs: 200,
          },
        },
      },
    },
  },
  networks: {
    hardhatMainnet: {
      type: "edr-simulated",
      chainType: "l1",
    },
    hardhatOp: {
      type: "edr-simulated",
      chainType: "op",
    },
    localhost: {
      type: "http",
      chainType: "l1",
      url: "http://127.0.0.1:8545",
    },
    chainA: {
      type: "http",
      chainType: "l1",
      url: "http://127.0.0.1:8545",
    },
    chainB: {
      type: "http",
      chainType: "l1",
      url: "http://127.0.0.1:8546",
    },
    chainC: {
      type: "http",
      chainType: "l1",
      url: "http://127.0.0.1:8547",
    },
    chainD: {
      type: "http",
      chainType: "l1",
      url: "http://127.0.0.1:8548",
    },
    sourceTestnet: {
      type: "http",
      chainType: "l1",
      url: configVariable("SOURCE_RPC_URL"),
      accounts: [configVariable("DEPLOYER_PRIVATE_KEY")],
    },
    destinationTestnet: {
      type: "http",
      chainType: "l1",
      url: configVariable("DESTINATION_RPC_URL"),
      accounts: [configVariable("DEPLOYER_PRIVATE_KEY")],
    },
  },
});
