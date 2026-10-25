import { Suspense } from "react";

import { LoadingRows } from "@/components/registry";

import { CatalogView } from "./catalog-view";

export default async function CatalogPage({ params }: PageProps<"/instances/[id]/catalog">) {
  const { id } = await params;
  return (
    <Suspense fallback={<LoadingRows />}>
      <CatalogView id={id} />
    </Suspense>
  );
}
