import { Topbar } from "@/components/topbar";
import { N8NWorkflowStudio } from "@/components/n8n-workflow-studio";

export default function DashboardPage() {
  return (
    <main>
      <Topbar />
      <N8NWorkflowStudio />
    </main>
  );
}

