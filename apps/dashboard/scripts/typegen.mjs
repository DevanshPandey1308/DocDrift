import { readFile, writeFile } from "node:fs/promises";
import openapiTS, { astToString } from "openapi-typescript";

const baseUrl = process.env.VITE_API_BASE_URL ?? await envBaseUrl() ?? "http://localhost:8000";
const schemaUrl = new URL("/openapi.json", baseUrl);
const output = new URL("../src/api/schema.d.ts", import.meta.url);

const types = await openapiTS(schemaUrl);
await writeFile(output, astToString(types), "utf8");
console.log(`Generated API types from ${schemaUrl.href}`);

async function envBaseUrl() {
  try {
    const contents = await readFile(new URL("../.env.local", import.meta.url), "utf8");
    const match = contents.match(/^\s*VITE_API_BASE_URL\s*=\s*["']?([^\r\n"']+)/m);
    return match?.[1]?.trim();
  } catch {
    return undefined;
  }
}
