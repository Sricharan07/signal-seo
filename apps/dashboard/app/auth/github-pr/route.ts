import { ownerConnectorPost } from "@/lib/owner-connector-route";

export async function POST(request: Request): Promise<Response> {
  return ownerConnectorPost(request, "github-pr");
}
