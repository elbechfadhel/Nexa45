"use client";

import Link from "next/link";

import { CapabilityBadge, ConnectionStatus } from "@/components/instances";
import { ErrorBox, formatDate, formatInt, LoadingRows } from "@/components/registry";
import { environmentTone, StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useInstances } from "@/lib/api/hooks";
import { useUrlState } from "@/lib/use-url-state";

import { CreateInstanceForm } from "./create-instance-form";

export function InstancesView() {
  const { data, error, isPending } = useInstances();
  const url = useUrlState();
  const creating = url.get("new") === "1";
  return (
    <div className="page-stack">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="space-y-1.5">
          <h1 className="hero-badge">Instances</h1>
          <p className="page-description">
            Real customer installations, each attached to a standard version. Whether an instance is used as a
            source or a target follows from its verified capabilities, not from a fixed role.
          </p>
        </div>
        {!creating && <Button onClick={() => url.set({ new: 1 })}>New instance</Button>}
      </div>
      {creating && <CreateInstanceForm onClose={() => url.set({ new: undefined })} />}
      {error && <ErrorBox error={error} />}
      {isPending && <LoadingRows />}
      {data?.length === 0 && (
        <p className="meta">
          No instance yet. Use &ldquo;New instance&rdquo;, or the CLI:{" "}
          <code>uv run uie instance add -n … -s odoo -v 17 --url … --db … -u …</code>
        </p>
      )}
      {!!data?.length && (
        <Card className="py-0">
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="pl-4">Instance</TableHead>
                  <TableHead>Supplier</TableHead>
                  <TableHead>System</TableHead>
                  <TableHead>Interface</TableHead>
                  <TableHead>Env.</TableHead>
                  <TableHead>Connection</TableHead>
                  <TableHead>Last discovery</TableHead>
                  <TableHead>Capabilities</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.map((i) => (
                  <TableRow key={i.id}>
                    <TableCell className="pl-4">
                      <Link href={`/instances/${i.id}`} className="font-medium hover:underline">
                        {i.name}
                      </Link>
                      <div className="meta tech">{i.connection?.endpoint}</div>
                    </TableCell>
                    <TableCell>
                      <Link href={`/admin/suppliers/${i.organization_id}`} className="hover:underline">
                        {i.organization_name}
                      </Link>
                    </TableCell>
                    <TableCell>
                      <span className="font-medium capitalize">
                        {i.system} {i.version}
                      </span>
                      {i.edition && <span className="meta"> {i.edition}</span>}
                    </TableCell>
                    <TableCell className="tech">{i.interface}</TableCell>
                    <TableCell>
                      <StatusBadge tone={environmentTone(i.environment)}>{i.environment}</StatusBadge>
                    </TableCell>
                    <TableCell className="space-y-1">
                      <ConnectionStatus instance={i} />
                      {i.connection?.last_test_at && (
                        <div className="meta">{formatDate(i.connection.last_test_at)}</div>
                      )}
                    </TableCell>
                    <TableCell className="text-sm">
                      {i.last_discovery ? (
                        <>
                          <span className="tech">#{i.last_discovery.sequence}</span> ·{" "}
                          {formatDate(i.last_discovery.discovered_at)}
                          <div className="text-muted-foreground">
                            {formatInt(i.last_discovery.entity_count)} entities ·{" "}
                            {formatInt(i.last_discovery.field_count)} fields
                          </div>
                        </>
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </TableCell>
                    <TableCell>
                      <div className="flex max-w-80 flex-wrap gap-1">
                        {i.capabilities.map((c) => (
                          <CapabilityBadge key={c.capability} cap={c} compact />
                        ))}
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
