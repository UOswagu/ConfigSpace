#!/usr/bin/env python

##
# wrapping: A program making it easy to use hyperparameter
# optimization software.
# Copyright (C) 2013 Katharina Eggensperger and Matthias Feurer
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

__authors__ = ["Katharina Eggensperger", "Matthias Feurer", "Mohsin Ali"]
__contact__ = "automl.org"

from collections import OrderedDict
from itertools import product
from ConfigSpace.configuration_space import ConfigurationSpace
from ConfigSpace.hyperparameters import CategoricalHyperparameter, \
    UniformIntegerHyperparameter, UniformFloatHyperparameter, \
    NumericalHyperparameter, Constant, IntegerHyperparameter, \
    NormalIntegerHyperparameter, NormalFloatHyperparameter
from ConfigSpace.conditions import EqualsCondition, NotEqualsCondition, \
    InCondition, AndConjunction, OrConjunction, ConditionComponent
from ConfigSpace.forbidden import ForbiddenEqualsClause, \
    ForbiddenAndConjunction, ForbiddenInClause, AbstractForbiddenComponent, MultipleValueForbiddenClause
import sys
import pyparsing
import six

# Build pyparsing expressions for params
pp_param_name = pyparsing.Word(pyparsing.alphanums + "_" + "-" + "@" + "." + ":" + ";" + "\\" + "/" + "?" + "!" +
                               "$" + "%" + "&" + "*" + "+" + "<" + ">")
pp_digits = "0123456789"
pp_plusorminus = pyparsing.Literal('+') | pyparsing.Literal('-')
pp_int = pyparsing.Combine(pyparsing.Optional(pp_plusorminus) + pyparsing.Word(pp_digits))
pp_float = pyparsing.Combine(pyparsing.Optional(pp_plusorminus) + pyparsing.Optional(pp_int) + "." + pp_int)
pp_eorE = pyparsing.Literal('e') | pyparsing.Literal('E')
pp_floatorint = pp_float | pp_int
pp_e_notation = pyparsing.Combine(pp_floatorint + pp_eorE + pp_int)
pp_number = pp_e_notation | pp_float | pp_int
pp_numberorname = pp_number | pp_param_name
pp_il = pyparsing.Word("il")
pp_choices = pp_param_name + pyparsing.Optional(pyparsing.OneOrMore("," + pp_param_name))

pp_cont_param = pp_param_name + "[" + pp_number + "," + pp_number + "]" + \
                "[" + pp_number + "]" + pyparsing.Optional(pp_il)
pp_cat_param = pp_param_name + "{" + pp_choices + "}" + "[" + pp_param_name + "]"
pp_condition = pp_param_name + "|" + pp_param_name + "in" + "{" + pp_choices + "}"
pp_forbidden_clause = "{" + pp_param_name + "=" + pp_numberorname + \
                      pyparsing.Optional(pyparsing.OneOrMore("," + pp_param_name + "=" + pp_numberorname)) + "}"


def build_categorical(param):
    cat_template = "%s '--%s ' c (%s)"
    return cat_template % (param.name, param.name,
                           ",".join([str(value) for value in param.choices]))


def build_constant(param):
    constant_template = "%s '--%s ' c (%s)"
    return constant_template % (param.name, param.name, param.value)


def build_continuous(param):
    if type(param) in (NormalIntegerHyperparameter,
                       NormalFloatHyperparameter):
        param = param.to_uniform()

    float_template = "%s '--%s ' r (%s, %s) "
    int_template = "%s '--%s ' i (%d, %d)"
    # if param.log:
    #     float_template += "l"
    #     int_template += "l"

    if param.q is not None:
        q_prefix = "Q%d_" % (int(param.q),)
    else:
        q_prefix = ""
    default = param.default

    if isinstance(param, IntegerHyperparameter):
        default = int(default)
        return int_template % (param.name, param.name, param.lower,
                               param.upper)
    else:
        return float_template % (param.name, param.name, str(param.lower),
                                 str(param.upper))


def build_condition(condition):
    if not isinstance(condition, ConditionComponent):
        raise TypeError("build_condition must be called with an instance of "
                        "'%s', got '%s'" %
                        (ConditionComponent, type(condition)))

    # Check if IRACE can handle the condition
    if isinstance(condition, OrConjunction):
        raise NotImplementedError("IRACE cannot handle OR conditions: %s" %
                                  (condition))
    if isinstance(condition, NotEqualsCondition):
        raise NotImplementedError("IRACE cannot handle != conditions: %s" %
                                  (condition))

    # Findout type of parent
    pType = "i"
    if condition.parent.__class__.__name__ == 'CategoricalHyperparameter':
        pType = 'c'
    if condition.parent.__class__.__name__ == 'UniformFloatHyperparameter':
        pType = 'r'

    # Now handle the conditions SMAC can handle
    condition_template = "%s | %s %%in%% %s(%s) "
    if isinstance(condition, AndConjunction):
        raise NotImplementedError("This is not yet implemented!")
    elif isinstance(condition, InCondition):
        return condition_template % (condition.child.name,
                                     condition.parent.name,
                                     pType,
                                     ", ".join(condition.values))
    elif isinstance(condition, EqualsCondition):
        return condition_template % (condition.child.name,
                                     condition.parent.name,
                                     pType,
                                     condition.value)


def build_forbidden(clause):
    if not isinstance(clause, AbstractForbiddenComponent):
        raise TypeError("build_forbidden must be called with an instance of "
                        "'%s', got '%s'" %
                        (AbstractForbiddenComponent, type(clause)))

    if not isinstance(clause, (ForbiddenEqualsClause, ForbiddenAndConjunction)):
        raise NotImplementedError("SMAC cannot handle '%s' of type %s" %
                                  str(clause), (type(clause)))

    retval = six.StringIO()
    retval.write("{")
    # Really simple because everything is an AND-conjunction of equals
    # conditions
    dlcs = clause.get_descendant_literal_clauses()
    for dlc in dlcs:
        if retval.tell() > 1:
            retval.write(", ")
        retval.write("%s=%s" % (dlc.hyperparameter.name, dlc.value))
    retval.write("}")
    retval.seek(0)
    return retval.getvalue()


def read(pcs_string, debug=False):
    configuration_space = ConfigurationSpace()
    conditions = []
    forbidden = []

    # some statistics
    ct = 0
    cont_ct = 0
    cat_ct = 0
    line_ct = 0

    for line in pcs_string:
        line_ct += 1

        if "#" in line:
            # It contains a comment
            pos = line.find("#")
            line = line[:pos]

        # Remove quotes and whitespaces at beginning and end
        line = line.replace('"', "").replace("'", "")
        line = line.strip()

        if "|" in line:
            # It's a condition
            try:
                c = pp_condition.parseString(line)
                conditions.append(c)
            except pyparsing.ParseException:
                raise NotImplementedError("Could not parse condition: %s" % line)
            continue
        if "}" not in line and "]" not in line:
            continue
        if line.startswith("{") and line.endswith("}"):
            forbidden.append(line)
            continue
        if len(line.strip()) == 0:
            continue

        ct += 1
        param = None

        create = {"int": UniformIntegerHyperparameter,
                  "float": UniformFloatHyperparameter,
                  "categorical": CategoricalHyperparameter}

        try:
            param_list = pp_cont_param.parseString(line)
            il = param_list[9:]
            if len(il) > 0:
                il = il[0]
            param_list = param_list[:9]
            name = param_list[0]
            lower = float(param_list[2])
            upper = float(param_list[4])
            paramtype = "int" if "i" in il else "float"
            log = True if "l" in il else False
            default = float(param_list[7])
            param = create[paramtype](name=name, lower=lower, upper=upper,
                                      q=None, log=log, default=default)
            cont_ct += 1
        except pyparsing.ParseException:
            pass

        try:
            param_list = pp_cat_param.parseString(line)
            name = param_list[0]
            choices = [c for c in param_list[2:-4:2]]
            default = param_list[-2]
            param = create["categorical"](name=name, choices=choices,
                                          default=default)
            cat_ct += 1
        except pyparsing.ParseException:
            pass

        if param is None:
            raise NotImplementedError("Could not parse: %s" % line)

        configuration_space.add_hyperparameter(param)

    for clause in forbidden:
        # TODO test this properly!
        # TODO Add a try/catch here!
        # noinspection PyUnusedLocal
        param_list = pp_forbidden_clause.parseString(clause)
        tmp_list = []
        clause_list = []
        for value in param_list[1:]:
            if len(tmp_list) < 3:
                tmp_list.append(value)
            else:
                # So far, only equals is supported by SMAC
                if tmp_list[1] == '=':
                    # TODO maybe add a check if the hyperparameter is
                    # actually in the configuration space
                    clause_list.append(ForbiddenEqualsClause(
                        configuration_space.get_hyperparameter(tmp_list[0]),
                        tmp_list[2]))
                else:
                    raise NotImplementedError()
                tmp_list = []
        configuration_space.add_forbidden_clause(ForbiddenAndConjunction(
            *clause_list))

    # Now handle conditions
    # If there are two conditions for one child, these two conditions are an
    # AND-conjunction of conditions, thus we have to connect them
    conditions_per_child = OrderedDict()
    for condition in conditions:
        child_name = condition[0]
        if child_name not in conditions_per_child:
            conditions_per_child[child_name] = list()
        conditions_per_child[child_name].append(condition)

    for child_name in conditions_per_child:
        condition_objects = []
        for condition in conditions_per_child[child_name]:
            child = configuration_space.get_hyperparameter(child_name)
            parent_name = condition[2]
            parent = configuration_space.get_hyperparameter(parent_name)
            restrictions = condition[5:-1:2]

            # TODO: cast the type of the restriction!
            if len(restrictions) == 1:
                condition = EqualsCondition(child, parent, restrictions[0])
            else:
                condition = InCondition(child, parent, values=restrictions)
            condition_objects.append(condition)

        # Now we have all condition objects for this child, so we can build a
        #  giant AND-conjunction of them (if number of conditions >= 2)!

        if len(condition_objects) > 1:
            and_conjunction = AndConjunction(*condition_objects)
            configuration_space.add_condition(and_conjunction)
        else:
            configuration_space.add_condition(condition_objects[0])

    return configuration_space


def write(configuration_space):
    if not isinstance(configuration_space, ConfigurationSpace):
        raise TypeError("pcs_parser.write expects an instance of %s, "
                        "you provided '%s'" % (ConfigurationSpace,
                                               type(configuration_space)))

    param_lines = six.StringIO()
    condition_lines = six.StringIO()
    forbidden_lines = []
    for hyperparameter in configuration_space.get_hyperparameters():
        # Check if the hyperparameter names are valid IRACE names!
        try:
            pp_param_name.parseString(hyperparameter.name)
        except pyparsing.ParseException:
            raise ValueError(
                "Illegal hyperparameter name for IRACE: %s" % hyperparameter.name)

        # First build params
        if param_lines.tell() > 0:
            param_lines.write("\n")
        if isinstance(hyperparameter, NumericalHyperparameter):
            # print "building countinuous param"
            param_lines.write(build_continuous(hyperparameter))
        elif isinstance(hyperparameter, CategoricalHyperparameter):
            # print "building categorical param"
            param_lines.write(build_categorical(hyperparameter))
        elif isinstance(hyperparameter, Constant):
            # print "building constant param"
            param_lines.write(build_constant(hyperparameter))
        else:
            raise TypeError("Unknown type: %s (%s)" % (
                type(hyperparameter), hyperparameter))

    for condition in configuration_space.get_conditions():
        if condition_lines.tell() > 0:
            condition_lines.write("\n")
        condition_lines.write(build_condition(condition))

    for forbidden_clause in configuration_space.forbidden_clauses:
        # Convert in-statement into two or more equals statements
        dlcs = forbidden_clause.get_descendant_literal_clauses()
        # First, get all in statements and convert them to equal statements
        in_statements = []
        other_statements = []
        for dlc in dlcs:
            if isinstance(dlc, MultipleValueForbiddenClause):
                if not isinstance(dlc, ForbiddenInClause):
                    raise ValueError("IRACE cannot handle this forbidden "
                                     "clause: %s" % dlc)
                in_statements.append(
                    [ForbiddenEqualsClause(dlc.hyperparameter, value)
                     for value in dlc.values])
            else:
                other_statements.append(dlc)
        # Second, create the product of all elements in the IN statements,
        # create a ForbiddenAnd and add all ForbiddenEquals
        if len(in_statements) > 0:
            for i, p in enumerate(product(*in_statements)):
                all_forbidden_clauses = list(p) + other_statements
                f = ForbiddenAndConjunction(*all_forbidden_clauses)
                forbidden_lines.append(build_forbidden(f))
        else:
            forbidden_lines.append(build_forbidden(forbidden_clause))

    # Add condtions: first conver param_lines to array then search first part of condition in that array
    # if found append second part of condition to that array part
    splitted_params = param_lines.getvalue().split("\n")
    if condition_lines.tell() > 0:
        condition_lines.seek(0)
        param_lines.write("\n\n")
        for line in condition_lines:
            param_lines.write(line)
            t = filter(lambda x: line.split(" ")[0] in x, splitted_params)[0]
            index = splitted_params.index(t)
            splitted_params[index] = splitted_params[index] + "  ".join(line.split(" ")[1:])

    for i, j in enumerate(splitted_params):
        if j[-1] != "\n":
            splitted_params[i] += "\n"
    # print splitted_params

    # TODO: check if irace supports forbidden lines
    # if len(forbidden_lines) > 0:
    #     forbidden_lines.sort()
    #     param_lines.write("\n\n")
    #     for line in forbidden_lines:
    #         param_lines.write(line)
    #         param_lines.write("\n")

    # Check if the default configuration is a valid configuration!

    # overwrite param_lines with splitted_params which contains lines with conditions
    param_lines = six.StringIO()
    for l in splitted_params:
        param_lines.write(l)
    return param_lines.getvalue()
