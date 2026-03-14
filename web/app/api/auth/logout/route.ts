import { NextResponse } from "next/server";

export async function POST(req: Request) {
  const resp = NextResponse.redirect(new URL("/", req.url));
  resp.cookies.set("kan_console_token", "", { httpOnly: true, path: "/", maxAge: 0 });
  resp.cookies.set("kan_console_expires_at", "", { httpOnly: true, path: "/", maxAge: 0 });
  return resp;
}

