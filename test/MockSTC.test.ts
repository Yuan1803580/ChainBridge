import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { network } from "hardhat";

describe("MockSTC", async function () {
  const { viem } = await network.create();

  it("allows only the deployment owner to mint test liquidity", async function () {
    const [owner, other] = await viem.getWalletClients();
    const token = await viem.deployContract("MockSTC");

    await token.write.mint([other.account.address, 10n ** 18n], { account: owner.account });
    assert.equal(await token.read.balanceOf([other.account.address]), 10n ** 18n);

    await viem.assertions.revertWithCustomError(
      token.write.mint([other.account.address, 1n], { account: other.account }),
      token,
      "OwnableUnauthorizedAccount",
    );
  });
});
