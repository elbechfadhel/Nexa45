import { Suspense } from "react";

import { LoadingRows } from "@/components/registry";

import { SchemaBrowser } from "./schema-browser";

export default async function SnapshotPage({ params }: PageProps<"/snapshots/[id]">) {
  const { id } = await params;
  return (
    <Suspense fallback={<LoadingRows />}>
      <SchemaBrowser snapshotId={id} />
    </Suspense>
  );
}
