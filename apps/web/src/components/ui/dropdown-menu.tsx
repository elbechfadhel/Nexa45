"use client";

/** Menu déroulant minimal sur @base-ui/react/menu (même esprit que les composants shadcn du dossier). */
import { Menu } from "@base-ui/react/menu";
import { Check } from "lucide-react";

import { cn } from "@/lib/utils";

export const DropdownMenu = Menu.Root;

export function DropdownMenuTrigger({ className, ...props }: Menu.Trigger.Props) {
  return <Menu.Trigger className={cn("outline-none", className)} {...props} />;
}

export function DropdownMenuContent({
  className,
  align = "end",
  sideOffset = 4,
  children,
  ...props
}: Menu.Popup.Props & Pick<Menu.Positioner.Props, "align" | "sideOffset">) {
  return (
    <Menu.Portal>
      <Menu.Positioner align={align} sideOffset={sideOffset} className="isolate z-50">
        <Menu.Popup
          className={cn(
            // .menu du pack (rayon 16px, ombre de menu)
            "menu min-w-44 text-sm outline-none",
            className,
          )}
          {...props}
        >
          {children}
        </Menu.Popup>
      </Menu.Positioner>
    </Menu.Portal>
  );
}

const itemClass =
  "menu-item cursor-default outline-none select-none data-highlighted:bg-muted data-highlighted:text-foreground data-disabled:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0";

export function DropdownMenuItem({
  className,
  variant,
  ...props
}: Menu.Item.Props & { variant?: "destructive" }) {
  return (
    <Menu.Item
      className={cn(itemClass, variant === "destructive" && "menu-item-danger data-highlighted:bg-danger/10 data-highlighted:text-danger", className)}
      {...props}
    />
  );
}

export function DropdownMenuCheckboxItem({ className, children, ...props }: Menu.CheckboxItem.Props) {
  return (
    <Menu.CheckboxItem className={cn(itemClass, "pl-7 relative", className)} {...props}>
      <Menu.CheckboxItemIndicator className="absolute left-2">
        <Check />
      </Menu.CheckboxItemIndicator>
      {children}
    </Menu.CheckboxItem>
  );
}

export function DropdownMenuLabel({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("px-2 py-1 text-xs font-medium text-muted-foreground", className)} {...props} />;
}

export function DropdownMenuSeparator({ className }: { className?: string }) {
  return <div role="separator" className={cn("-mx-1 my-1 h-px bg-border", className)} />;
}
