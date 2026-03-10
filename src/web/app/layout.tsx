import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "다올투자증권 플랫폼전략본부 Knowledge Lake",
  description: "플랫폼전략본부 지식 검색",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ko">
      <body className="bg-gray-50 text-gray-900 min-h-screen">{children}</body>
    </html>
  );
}
