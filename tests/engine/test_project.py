import os
from unittest import mock
from unittest.mock import Mock, call, patch, mock_open

import pytest

from brickflow.context import ctx, BrickflowInternalVariables
from brickflow.engine.compute import Cluster
from brickflow.engine.project import (
    Project,
    Stage,
    WorkflowAlreadyExistsError,
    get_caller_info,
    ExecuteError,
)
from brickflow.codegen import GitRepoIsDirtyError
from brickflow import BrickflowEnvVars, BrickflowDefaultEnvs
from brickflow.engine.workflow import Workflow
from brickflow.tf import (  # noqa needed for import and jsii metadata being properly loading
    databricks,
)
from tests.engine.sample_workflow import wf, task_function


def side_effect(a, _):  # noqa
    if a == BrickflowInternalVariables.workflow_id.value:
        return wf.name
    if a == BrickflowInternalVariables.task_id.value:
        return task_function.__name__


def dynamic_side_effect_return(custom_var, custom_return):
    def side_effect_return(a, _):  # noqa
        existing_side_effect = side_effect(a, _)
        if existing_side_effect is not None:
            return existing_side_effect
        if a == custom_var:
            return custom_return

    return side_effect_return


class TestProject:
    @patch("brickflow.context.ctx.dbutils_widget_get_or_else")
    def test_project_execute(self, dbutils):
        dbutils.side_effect = side_effect
        with Project("test-project") as f:
            f.add_workflow(wf)
        assert ctx.get_return_value(task_key=task_function) == task_function()

    @patch("brickflow.context.ctx.dbutils_widget_get_or_else")
    def test_project_execute_custom_param(self, dbutils):
        # this assumes that in the databricks job ui you provide a custom value
        dbutils.side_effect = dynamic_side_effect_return("test", "helloworld")
        with Project("test-project") as f:
            f.add_workflow(wf)
        assert ctx.get_return_value(task_key=task_function) == task_function(
            test="helloworld"
        )

    @mock.patch.dict(
        os.environ,
        {
            BrickflowEnvVars.BRICKFLOW_MODE.value: Stage.deploy.value,
            BrickflowEnvVars.BRICKFLOW_ENV.value: "dev",
        },
    )
    @patch("pathlib.Path.open", new_callable=mock_open, read_data="data")
    @patch("subprocess.check_output")
    @patch("brickflow.context.ctx.dbutils_widget_get_or_else")
    def test_project_deploy(self, dbutils: Mock, subproc: Mock, mock_open_file: Mock):
        dbutils.side_effect = side_effect
        git_ref_b = b"a"
        git_repo = "https://github.com/"
        git_provider = "github"
        subproc.return_value = git_ref_b

        with Project("test-project1", git_repo=git_repo, provider=git_provider) as f:
            f.add_workflow(wf)

        # default path uses commit
        assert f.git_reference == "commit/" + git_ref_b.decode("utf-8")
        assert f.git_repo == git_repo
        assert f.provider == git_provider

        mock_open_file.assert_called()
        subproc.assert_has_calls(
            [  # noqa
                call(['git log -n 1 --pretty=format:"%H"'], shell=True),
                call(["git diff --stat"], shell=True),
            ]
        )

    @mock.patch.dict(
        os.environ,
        {
            BrickflowEnvVars.BRICKFLOW_MODE.value: Stage.deploy.value,
            BrickflowEnvVars.BRICKFLOW_ENV.value: BrickflowDefaultEnvs.LOCAL.value,
        },
    )
    @patch("subprocess.check_output")
    @patch("brickflow.context.ctx.dbutils_widget_get_or_else")
    def test_project_deploy_is_git_dirty_error(self, dbutils: Mock, subproc: Mock):
        dbutils.side_effect = side_effect
        resp = b"some really long path must return git dirty error"
        git_repo = "https://github.com/"
        git_provider = "github"
        subproc.return_value = resp

        with pytest.raises(GitRepoIsDirtyError):
            with Project(
                "test-project1", git_repo=git_repo, provider=git_provider
            ) as f:
                f.add_workflow(wf)

    @patch("brickflow.context.ctx.dbutils_widget_get_or_else")
    def test_project_workflow_already_exists_error(self, dbutils):
        dbutils.side_effect = side_effect
        with pytest.raises(ExecuteError) as err:
            with Project("test-project") as f:
                f.add_workflow(wf)
                f.add_workflow(wf)
            assert hasattr(err, "__cause__") and isinstance(
                err.__cause__, WorkflowAlreadyExistsError
            )

    def test_project_workflow_no_workflows_skip(self):
        with Project("test-project"):
            pass

    @patch("brickflow.context.ctx.dbutils_widget_get_or_else")
    def test_project_workflow_no_workflow_task_id_skip(self, dbutils):
        dbutils.return_value = None

        with Project(
            "test-project",
        ) as f:
            f.add_workflow(wf)

    @mock.patch.dict(
        os.environ, {BrickflowEnvVars.BRICKFLOW_MODE.value: Stage.deploy.value}
    )
    @patch("subprocess.check_output")
    @patch("brickflow.context.ctx.dbutils_widget_get_or_else")
    def test_project_deploy_workflow_no_schedule(self, dbutils: Mock, subproc: Mock):
        dbutils.return_value = (
            "local"  # needs to let the workflow know it a local deployment
        )

        with Project(
            "test-project",
        ) as f:
            f.add_workflow(
                Workflow(
                    "my-workflow",
                    default_cluster=Cluster.from_existing_cluster("someid"),
                )
            )
        subproc.assert_called()

    @mock.patch.dict(
        os.environ,
        {
            BrickflowEnvVars.BRICKFLOW_MODE.value: Stage.deploy.value,
            BrickflowEnvVars.BRICKFLOW_ENV.value: BrickflowDefaultEnvs.LOCAL.value,
        },
    )
    @patch("subprocess.check_output")
    @patch("brickflow.context.ctx.dbutils_widget_get_or_else")
    def test_project_deploy_local_mode(self, dbutils: Mock, subproc: Mock):
        dbutils.return_value = None

        with Project(
            "test-project",
        ) as f:
            f.add_workflow(
                Workflow(
                    "my-workflow",
                    default_cluster=Cluster.from_existing_cluster("someid"),
                )
            )
        subproc.assert_called()

    @mock.patch.dict(
        os.environ,
        {
            BrickflowEnvVars.BRICKFLOW_MODE.value: Stage.deploy.value,
            BrickflowEnvVars.BRICKFLOW_ENV.value: "dev",
        },
    )
    @patch("pathlib.Path.open", new_callable=mock_open, read_data="data")
    @patch("subprocess.check_output")
    @patch("brickflow.context.ctx.dbutils_widget_get_or_else")
    def test_project_workflow_deploy_batch_false(
        self, dbutils: Mock, sub_proc_mock: Mock, mock_open_file: Mock
    ):
        dbutils.return_value = None
        sub_proc_mock.return_value = b""
        with Project("test-project", batch=False) as f:
            f.add_workflow(wf)

        mock_open_file.assert_called()

    @patch("brickflow.context.ctx.dbutils_widget_get_or_else")
    def test_adding_pkg(self, dbutils):
        from tests import sample_workflows

        dbutils.side_effect = side_effect
        with Project("test-project") as f:
            f.add_pkg(sample_workflows)

    # @mock.patch.dict(
    #     os.environ,
    #     {
    #         BrickflowEnvVars.BRICKFLOW_ENV.value: "something-not-local",
    #     },
    # )
    # @patch("brickflow.context.ctx.dbutils_widget_get_or_else")
    # def test_should_use_s3_backend_not_local(self, dbutils):
    #     from examples import sample_workflows
    #
    #     dbutils.side_effect = side_effect
    #     with Project("test-project", s3_backend={}) as f:
    #         f.add_pkg(sample_workflows)
    #
    #     assert f.should_use_s3_backend() is True, "Should use s3 backend"
    #
    #     with Project("test-project") as f:
    #         f.add_pkg(sample_workflows)
    #
    #     assert f.should_use_s3_backend() is False, "Should not use s3 backend"
    #
    # @mock.patch.dict(
    #     os.environ,
    #     {
    #         BrickflowEnvVars.BRICKFLOW_ENV.value: "local",
    #     },
    # )
    # @patch("brickflow.context.ctx.dbutils_widget_get_or_else")
    # def test_should_use_s3_backend_local(self, dbutils):
    #     from examples import sample_workflows
    #
    #     dbutils.side_effect = side_effect
    #     with Project("test-project", s3_backend={}) as f:
    #         f.add_pkg(sample_workflows)
    #
    #     assert f.should_use_s3_backend() is False, "Should not use s3 backend"
    #
    #     with Project("test-project") as f:
    #         f.add_pkg(sample_workflows)
    #     assert f.should_use_s3_backend() is False, "Should not use s3 backend"

    @mock.patch.dict(
        os.environ,
        {
            BrickflowEnvVars.BRICKFLOW_S3_BACKEND_BUCKET.value: "s3://some-bucket/",
            BrickflowEnvVars.BRICKFLOW_S3_BACKEND_REGION.value: "us-east-1",
            BrickflowEnvVars.BRICKFLOW_S3_BACKEND_KEY.value: "some-key",
            BrickflowEnvVars.BRICKFLOW_S3_BACKEND_DYNAMODB_TABLE.value: "some-dynamo-table",
        },
    )
    def test_set_s3_backend_env_variables_default(self):
        project = Project("test-project", s3_backend=None)
        assert project.s3_backend == {
            "bucket": "s3://some-bucket/",
            "key": "some-key",
            "region": "us-east-1",
            "dynamodb_table": "some-dynamo-table",
        }

        project = Project("test-project", s3_backend={})
        assert project.s3_backend == {}

    @mock.patch.dict(
        os.environ,
        {
            BrickflowEnvVars.BRICKFLOW_S3_BACKEND_BUCKET.value: "s3://some-bucket/",
            BrickflowEnvVars.BRICKFLOW_S3_BACKEND_REGION.value: "us-east-1",
            BrickflowEnvVars.BRICKFLOW_S3_BACKEND_KEY.value: "some-key",
            BrickflowEnvVars.BRICKFLOW_S3_BACKEND_DYNAMODB_TABLE.value: "some-dynamo-table",
        },
    )
    def test_set_s3_backend_env_cdktf_no_error(self):
        project = Project("test-project", s3_backend=None)
        from cdktf import App, TerraformStack, S3Backend

        app = App()
        stack = TerraformStack(app, "some_random_id")
        S3Backend(stack, **project.s3_backend)

    def test_set_s3_backend_env_variables_missing_default(self):
        project = Project("test-project", s3_backend=None)
        assert project.s3_backend is None

    def test_adding_pkg_err(self):
        fake_pkg = Mock()
        setattr(fake_pkg, "__file__", None)
        with pytest.raises(ExecuteError) as err:
            with Project("test-project") as f:
                f.add_pkg(fake_pkg)
            assert hasattr(err, "__cause__") and isinstance(err.__cause__, ImportError)

    @patch("inspect.stack")
    def test_get_caller_info(self, inspect_mock: Mock):
        inspect_mock.return_value = []
        assert get_caller_info() is None
        inspect_mock.assert_called_once()
