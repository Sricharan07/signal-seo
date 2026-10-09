import { bingConnectorCallback } from "@/lib/owner-connector-route";

export async function GET(request: Request): Promise<Response> {
  return bingConnectorCallback(request);
}
