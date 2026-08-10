import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("./contact.js", () => ({
  handleContact: vi.fn(),
}));

import { handleContact } from "./contact.js";
import worker from "./index.js";

describe("worker fetch router", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("routes POST /api/contact to handleContact", async () => {
    handleContact.mockResolvedValue(new Response("ok"));
    const request = new Request("https://flowersbytiana.com/api/contact", { method: "POST" });
    const env = { ASSETS: { fetch: vi.fn() } };

    await worker.fetch(request, env);

    expect(handleContact).toHaveBeenCalledWith(request, env);
    expect(env.ASSETS.fetch).not.toHaveBeenCalled();
  });

  it("falls through to static assets for any other request", async () => {
    const assetsResponse = new Response("<html>page</html>");
    const env = { ASSETS: { fetch: vi.fn().mockResolvedValue(assetsResponse) } };
    const request = new Request("https://flowersbytiana.com/albums/orchids/");

    const response = await worker.fetch(request, env);

    expect(env.ASSETS.fetch).toHaveBeenCalledWith(request);
    expect(response).toBe(assetsResponse);
  });

  it("falls through when /api/contact is requested with a non-POST method", async () => {
    const assetsResponse = new Response("not found", { status: 404 });
    const env = { ASSETS: { fetch: vi.fn().mockResolvedValue(assetsResponse) } };
    const request = new Request("https://flowersbytiana.com/api/contact", { method: "GET" });

    const response = await worker.fetch(request, env);

    expect(handleContact).not.toHaveBeenCalled();
    expect(env.ASSETS.fetch).toHaveBeenCalledWith(request);
    expect(response).toBe(assetsResponse);
  });
});
