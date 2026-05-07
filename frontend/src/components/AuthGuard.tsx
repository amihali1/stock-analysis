"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { isAuthenticated, logout } from "@/lib/api";

export default function AuthGuard({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    if (pathname === "/login") {
      setChecked(true);
      return;
    }
    if (!isAuthenticated()) {
      const next =
        typeof window !== "undefined"
          ? window.location.pathname + window.location.search
          : pathname;
      const target =
        next && next !== "/login"
          ? `/login?next=${encodeURIComponent(next)}`
          : "/login";
      router.replace(target);
    } else {
      setChecked(true);
    }
  }, [pathname, router]);

  if (!checked && pathname !== "/login") {
    return null; // Don't flash content before auth check
  }

  return <>{children}</>;
}

export function LogoutButton() {
  const pathname = usePathname();

  if (pathname === "/login") return null;

  async function handleLogout() {
    try {
      await logout();
    } finally {
      window.location.replace("/login");
    }
  }

  return (
    <button
      onClick={handleLogout}
      className="text-gray-500 hover:text-gray-300 text-sm transition-colors ml-auto"
    >
      Logout
    </button>
  );
}
