"use client";

import { useParams } from "next/navigation";
import { TicketView } from "@/components/TicketView";

export default function TicketPage() {
  const { id } = useParams<{ id: string }>();
  return <TicketView id={id} />;
}
