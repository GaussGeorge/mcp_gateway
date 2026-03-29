package plangate

import "fmt"

// HTTPDAGStep DAG 执行计划中的单个步骤
type HTTPDAGStep struct {
	StepID    string   `json:"step_id"`
	ToolName  string   `json:"tool_name"`
	DependsOn []string `json:"depends_on,omitempty"`
}

// HTTPDAGPlan 完整的 DAG 执行计划
type HTTPDAGPlan struct {
	SessionID string        `json:"session_id"`
	Steps     []HTTPDAGStep `json:"steps"`
	Budget    int64         `json:"budget"`
}

// validateHTTPDAG 使用 Kahn 算法验证 DAG 无环
func validateHTTPDAG(plan *HTTPDAGPlan) error {
	inDegree := make(map[string]int)
	adj := make(map[string][]string)
	stepSet := make(map[string]bool)

	for _, step := range plan.Steps {
		stepSet[step.StepID] = true
		if _, ok := inDegree[step.StepID]; !ok {
			inDegree[step.StepID] = 0
		}
		for _, dep := range step.DependsOn {
			adj[dep] = append(adj[dep], step.StepID)
			inDegree[step.StepID]++
		}
	}

	for _, step := range plan.Steps {
		for _, dep := range step.DependsOn {
			if !stepSet[dep] {
				return fmt.Errorf("步骤 %s 依赖不存在的步骤 %s", step.StepID, dep)
			}
		}
	}

	queue := []string{}
	for id, deg := range inDegree {
		if deg == 0 {
			queue = append(queue, id)
		}
	}

	visited := 0
	for len(queue) > 0 {
		node := queue[0]
		queue = queue[1:]
		visited++
		for _, next := range adj[node] {
			inDegree[next]--
			if inDegree[next] == 0 {
				queue = append(queue, next)
			}
		}
	}

	if visited != len(plan.Steps) {
		return fmt.Errorf("DAG 计划中存在循环依赖")
	}
	return nil
}
