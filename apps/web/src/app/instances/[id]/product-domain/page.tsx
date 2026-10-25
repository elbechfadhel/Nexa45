import { Suspense } from "react";

import { LoadingRows } from "@/components/registry";

import { ProductDomainView } from "./product-domain-view";

export default async function ProductDomainPage({ params }: PageProps<"/instances/[id]/product-domain">) {
  const { id } = await params;
  return (
    <Suspense fallback={<LoadingRows />}>
      <ProductDomainView id={id} />
    </Suspense>
  );
}
