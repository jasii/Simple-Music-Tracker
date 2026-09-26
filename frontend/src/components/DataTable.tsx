// Headless table built on TanStack Table (@tanstack/react-table), rendered
// through the shadcn Table primitives. Pagination, sorting and row-selection
// state are owned by the library; the pager below is driven entirely by the
// TanStack table API (firstPage/previousPage/nextPage/lastPage + getCanPreviousPage etc.).
import { useEffect, useState } from "react";
import {
  LuChevronDown,
  LuChevronLeft,
  LuChevronRight,
  LuChevronUp,
  LuChevronsLeft,
  LuChevronsRight,
} from "react-icons/lu";
import {
  flexRender,
  getCoreRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type OnChangeFn,
  type PaginationState,
  type RowData,
  type RowSelectionState,
  type SortingState,
} from "@tanstack/react-table";
import { Button } from "./ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "./ui/table";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "./ui/select";

// Per-column layout hints, read off columnDef.meta in the renderer.
declare module "@tanstack/react-table" {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  interface ColumnMeta<TData extends RowData, TValue> {
    width?: string;
    align?: "start" | "center" | "end";
  }
}

const alignCls = (a?: "start" | "center" | "end") =>
  a === "center" ? "text-center" : a === "end" ? "text-right" : "";

export interface PageSizeOption {
  label: string;
  value: string; // "all" expands to the full row count
}

export interface DataTableProps<T> {
  data: T[];
  columns: ColumnDef<T, any>[];
  getRowId?: (row: T) => string;
  enableRowSelection?: boolean;
  rowSelection?: RowSelectionState;
  onRowSelectionChange?: OnChangeFn<RowSelectionState>;
  // Sorting
  initialSorting?: SortingState;
  // Pagination
  initialPageSize?: number;
  pageSizeOptions?: PageSizeOption[]; // renders a page-size <Select> when set
  summary?: (shown: number, total: number) => string;
  // States
  loading?: boolean;
  loadingText?: string;
  emptyText?: string;
  // Image URL per row (e.g. album art). When set, the next page's images are
  // prefetched into the browser cache so paging forward paints instantly --
  // unrendered rows otherwise only start downloading art once switched to.
  prefetchUrl?: (row: T) => string | null | undefined;
}

export function DataTable<T>({
  data,
  columns,
  getRowId,
  enableRowSelection,
  rowSelection,
  onRowSelectionChange,
  initialSorting = [],
  initialPageSize = 50,
  pageSizeOptions,
  summary,
  loading,
  loadingText = "Loading...",
  emptyText = "Nothing to show.",
  prefetchUrl,
}: DataTableProps<T>) {
  const [sorting, setSorting] = useState<SortingState>(initialSorting);
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: initialPageSize });
  const [pageSizeChoice, setPageSizeChoice] = useState(String(initialPageSize));

  const table = useReactTable({
    data,
    columns,
    state: {
      sorting,
      pagination,
      ...(rowSelection ? { rowSelection } : {}),
    },
    getRowId,
    enableRowSelection,
    onRowSelectionChange,
    onSortingChange: setSorting,
    onPaginationChange: setPagination,
    // Don't snap back to page 1 on optimistic row edits; we clamp manually below.
    autoResetPageIndex: false,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
  });

  const pageCount = table.getPageCount();
  const pageIndex = table.getState().pagination.pageIndex;
  // Keep the current page in range as the data set shrinks (search/filter).
  useEffect(() => {
    if (pageIndex > pageCount - 1) table.setPageIndex(Math.max(0, pageCount - 1));
  }, [pageCount, pageIndex, table]);
  // Keep "All" tracking the live row count as data changes.
  useEffect(() => {
    if (pageSizeChoice === "all") table.setPageSize(data.length || 1);
  }, [data.length, pageSizeChoice, table]);

  // Warm the browser cache for the next page's images. Sorted rows (not the
  // paginated model) hold the full filtered set, so slicing one page ahead
  // matches exactly what nextPage() will render.
  useEffect(() => {
    if (!prefetchUrl) return;
    const { pageSize } = table.getState().pagination;
    const start = (pageIndex + 1) * pageSize;
    for (const row of table.getSortedRowModel().rows.slice(start, start + pageSize)) {
      const url = prefetchUrl(row.original);
      if (url) new Image().src = url;
    }
  }, [prefetchUrl, pageIndex, pagination.pageSize, data, sorting, table]);

  function changePageSize(value: string) {
    setPageSizeChoice(value);
    table.setPageSize(value === "all" ? data.length || 1 : Number(value));
    table.setPageIndex(0);
  }

  const colCount = table.getVisibleFlatColumns().length;
  const rows = table.getRowModel().rows;

  return (
    <>
      <Table>
        <TableHeader>
          {table.getHeaderGroups().map((hg) => (
            <TableRow key={hg.id}>
              {hg.headers.map((header) => {
                const meta = header.column.columnDef.meta;
                const canSort = header.column.getCanSort();
                const sortDir = header.column.getIsSorted();
                return (
                  <TableHead
                    key={header.id}
                    style={meta?.width ? { width: meta.width } : undefined}
                    className={(canSort ? "cursor-pointer select-none " : "") + alignCls(meta?.align)}
                    onClick={canSort ? header.column.getToggleSortingHandler() : undefined}
                  >
                    <span
                      className={
                        "inline-flex items-center gap-1 " + (meta?.align === "end" ? "justify-end" : "")
                      }
                    >
                      {header.isPlaceholder
                        ? null
                        : flexRender(header.column.columnDef.header, header.getContext())}
                      {sortDir === "asc" && <LuChevronUp />}
                      {sortDir === "desc" && <LuChevronDown />}
                    </span>
                  </TableHead>
                );
              })}
            </TableRow>
          ))}
        </TableHeader>
        <TableBody>
          {loading ? (
            <TableRow>
              <TableCell colSpan={colCount} className="text-muted-foreground">{loadingText}</TableCell>
            </TableRow>
          ) : rows.length === 0 ? (
            <TableRow>
              <TableCell colSpan={colCount} className="text-muted-foreground">{emptyText}</TableCell>
            </TableRow>
          ) : (
            rows.map((row) => (
              <TableRow key={row.id}>
                {row.getVisibleCells().map((cell) => {
                  const meta = cell.column.columnDef.meta;
                  return (
                    <TableCell key={cell.id} className={alignCls(meta?.align)}>
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </TableCell>
                  );
                })}
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>

      {(summary || pageSizeOptions || pageCount > 1) && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {summary && <span className="text-muted-foreground">{summary(rows.length, data.length)}</span>}
          {pageSizeOptions && (
            <Select value={pageSizeChoice} onValueChange={changePageSize}>
              <SelectTrigger size="sm" className="w-36">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {pageSizeOptions.map((item) => (
                  <SelectItem key={item.value} value={item.value}>
                    {item.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          <div className="flex-1" />
          {pageCount > 1 && (
            <div className="flex items-center gap-1">
              <Button
                aria-label="First page"
                size="icon-sm"
                variant="ghost"
                disabled={!table.getCanPreviousPage()}
                onClick={() => table.firstPage()}
              >
                <LuChevronsLeft />
              </Button>
              <Button
                aria-label="Previous page"
                size="icon-sm"
                variant="ghost"
                disabled={!table.getCanPreviousPage()}
                onClick={() => table.previousPage()}
              >
                <LuChevronLeft />
              </Button>
              <span className="px-1 text-sm whitespace-nowrap text-muted-foreground">
                Page {pageIndex + 1} of {pageCount}
              </span>
              <Button
                aria-label="Next page"
                size="icon-sm"
                variant="ghost"
                disabled={!table.getCanNextPage()}
                onClick={() => table.nextPage()}
              >
                <LuChevronRight />
              </Button>
              <Button
                aria-label="Last page"
                size="icon-sm"
                variant="ghost"
                disabled={!table.getCanNextPage()}
                onClick={() => table.lastPage()}
              >
                <LuChevronsRight />
              </Button>
            </div>
          )}
        </div>
      )}
    </>
  );
}
