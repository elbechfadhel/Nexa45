import { Suspense } from "react";

import { LoadingRows } from "@/components/registry";

import { SupplierView } from "./supplier-view";

export default async function SupplierPage({ params }: PageProps<"/admin/suppliers/[id]">) {
  const { id } = await params;
  return (
    <Suspense fallback={<LoadingRows />}>
      <SupplierView id={id} />
    </Suspense>
  );
}
