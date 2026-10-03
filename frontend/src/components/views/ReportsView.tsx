"use client";

import React from "react";
import { Flag } from "lucide-react";
import PageShell from "@/components/layout/PageShell";
import { ReportsPanel } from "@/components/ReportsPanel";

/** Admin > Reports: what students flagged on answers and questions (moved here from the dashboard). */
export default function ReportsView({ token }: { token: string | null }) {
  return (
    <PageShell title="Reports" icon={<Flag size={22} />}
      subtitle="Answers and questions students flagged, and keys the textbooks dispute (from harder versions and page references).">
      <ReportsPanel token={token} />
    </PageShell>
  );
}
